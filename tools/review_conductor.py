#!/usr/bin/env python3
"""Deterministic, exact-head Review Conductor for event-driven PR review rails."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import hmac
import json
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable


UTC = getattr(dt, "UTC", dt.timezone.utc)
CONFIG_SCHEMA = "smoky.review-conductor.config.v1"
INTERNAL_EVENT_SCHEMA = "smoky.review-conductor.event.v1"
STATUS_SCHEMA = "smoky.review-conductor.status.v1"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")
GENERATION_FD_ENV = "REVIEW_CONDUCTOR_GENERATION_FD"
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
OPENCLAW_EXACT_TUPLE_CONTRACT = "review-conductor-openclaw-v1"
OPENCLAW_REPORT_MAX_BYTES = 24 * 1024
WORKFLOW_READBACK_MAX_BYTES = 1024 * 1024
OPENCLAW_REPORT_UNAVAILABLE = {"missing", "oversized", "invalid_text"}
GITHUB_EVENT_TYPES = ("pull_request", "workflow_run", "issue_comment")
REREVIEW_COMMAND_RE = re.compile(
    r"(?i)(?:^|[\s])@clawsweeper(?:\[bot\])?\s+(rereview|re-review)(?=$|[\s.,!;:])"
)
MAINTAINER_ASSOCIATIONS = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})
REREVIEW_IN_FLIGHT_STATES = frozenset({"clawsweeper_queued", "clawsweeper_running"})
REREVIEW_CLOSED_STATES = frozenset({"closed", "closed_merged"})
REREVIEW_PREREQUISITE_STATES = frozenset(
    {
        "ci_running",
        "ci_failed",
        "openclaw_queued",
        "openclaw_running",
        "openclaw_failed",
        "openclaw_clean_draft",
    }
)
CLASSIFICATIONS = {
    "required_fix",
    "reject_false_positive",
    "defer",
    "human_gate",
}
INTERNAL_EVENT_FIELDS = {
    "openclaw.started": ({"request_id"}, set()),
    "openclaw.terminal": (
        {"request_id", "result", "finding_count", "reviewer_actor", "proof_ref"},
        {"proof_sha256", "artifact_digest", "review_epoch", "original_report"},
    ),
    "adjudication.completed": (
        {"request_id", "rail", "classifications", "reviewer_actor", "proof_ref"},
        {"repair_owner", "mutation_handoff", "review_epoch"},
    ),
    "clawsweeper.started": ({"workflow_run_id"}, set()),
    "clawsweeper.terminal": (
        {"workflow_run_id", "result", "finding_count", "reviewer_actor", "proof_ref"},
        {"proof_sha256"},
    ),
}


def validate_original_report(value: Any) -> dict[str, Any]:
    """Closed, optional native-output receipt; legacy terminals omit it."""
    if not isinstance(value, dict):
        raise ContractError("original_report must be a bounded receipt")
    status = value.get("status")
    if status == "available":
        require_exact_keys(value, {"status", "ref", "sha256"}, set(), "original_report")
        require_text(value["ref"], "original report ref", 500)
        if not isinstance(value["sha256"], str) or re.fullmatch(r"[0-9a-f]{64}", value["sha256"]) is None:
            raise ContractError("original report digest must be lowercase SHA-256")
    elif isinstance(status, str) and status in OPENCLAW_REPORT_UNAVAILABLE:
        require_exact_keys(value, {"status"}, set(), "original_report")
    else:
        raise ContractError("original report status is unsupported")
    return dict(value)


def generation_pass_fds(
    environment: dict[str, str] | os._Environ[str] | None = None,
) -> tuple[int, ...]:
    """Keep a supervised generation alive across every direct adapter subprocess."""
    source = os.environ if environment is None else environment
    raw = source.get(GENERATION_FD_ENV)
    if raw is None:
        return ()
    if not raw.isascii() or not raw.isdigit() or int(raw) < 3:
        raise ContractError("service generation descriptor is unavailable")
    descriptor = int(raw)
    try:
        os.fstat(descriptor)
    except OSError as exc:
        raise ContractError("service generation descriptor is unavailable") from exc
    return (descriptor,)


def preserve_generation_environment(
    child: dict[str, str],
    source: dict[str, str] | os._Environ[str] | None = None,
) -> dict[str, str]:
    """Copy only the generation selector into an otherwise allowlisted environment."""
    source_environment = os.environ if source is None else source
    descriptors = generation_pass_fds(source_environment)
    if not descriptors:
        return child
    preserved = dict(child)
    preserved[GENERATION_FD_ENV] = str(descriptors[0])
    return preserved


def run_generation_bound(
    runner: Callable[..., subprocess.CompletedProcess[str]],
    command: list[str],
    **kwargs: Any,
) -> subprocess.CompletedProcess[str]:
    """Run one adapter command without letting close_fds escape supervision."""
    inherited = generation_pass_fds()
    if kwargs.get("env") is not None:
        kwargs["env"] = preserve_generation_environment(kwargs["env"])
    kwargs["pass_fds"] = inherited
    return runner(command, **kwargs)


TERMINAL_RESULTS = {"clean", "findings", "failed", "human_gate"}


class ContractError(RuntimeError):
    """Input or state violated the closed Review Conductor contract."""


class AuthorityDenied(ContractError):
    """A current authority fence denied a side effect before transport began."""


def utc_now() -> str:
    return dt.datetime.now(tz=UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def require_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError(f"{label} must be an object")
    return value


def require_exact_keys(
    value: dict[str, Any], required: set[str], optional: set[str], label: str
) -> None:
    missing = required - value.keys()
    unknown = value.keys() - required - optional
    if missing:
        raise ContractError(f"{label} is missing fields: {', '.join(sorted(missing))}")
    if unknown:
        raise ContractError(f"{label} has unknown fields: {', '.join(sorted(unknown))}")


def require_text(value: Any, label: str, maximum: int = 500) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ContractError(f"{label} must be non-empty text of at most {maximum} characters")
    return value


def require_sha(value: Any, label: str) -> str:
    text = require_text(value, label, 40)
    if not SHA_RE.fullmatch(text):
        raise ContractError(f"{label} must be a lowercase full 40-character commit SHA")
    return text


def require_positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ContractError(f"{label} must be a positive integer")
    return value


def require_non_negative_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ContractError(f"{label} must be a non-negative integer")
    return value


def same_typed_value(observed: Any, expected: Any) -> bool:
    return type(observed) is type(expected) and observed == expected


def matches_typed_mapping(observed: dict[str, Any], expected: dict[str, Any]) -> bool:
    return all(same_typed_value(observed.get(key), value) for key, value in expected.items())


def has_openclaw_applied_p3_qualification(observed: dict[str, Any]) -> bool:
    return matches_typed_mapping(
        observed,
        {
            "native_max_priority": "P3",
            "applied_max_priority": "P3",
            "exact_tuple_qualified": True,
        },
    )


def declares_openclaw_exact_tuple_contract(config: dict[str, Any]) -> bool:
    return config.get("openclaw", {}).get("exact_tuple_contract") == OPENCLAW_EXACT_TUPLE_CONTRACT


def bound_openclaw_queue_epoch(action: sqlite3.Row, payload: dict[str, Any]) -> int:
    persisted = require_non_negative_int(action["review_epoch"], "OpenClaw action review_epoch")
    payload_epoch = require_non_negative_int(
        payload.get("review_epoch"), "OpenClaw payload review_epoch"
    )
    if payload_epoch != persisted:
        raise ContractError(
            "OpenClaw payload review_epoch does not match the persisted action identity"
        )
    return persisted


def require_github_timestamp(value: Any, label: str) -> str:
    text = require_text(value, label, 40)
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContractError(f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != dt.timedelta(0):
        raise ContractError(f"{label} must identify an exact UTC instant")
    return parsed.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ContractError(f"cannot read {label}: {exc.__class__.__name__}") from exc
    try:
        return require_object(json.loads(raw), label)
    except json.JSONDecodeError as exc:
        raise ContractError(f"{label} is not valid JSON") from exc


def load_config(path: Path) -> dict[str, Any]:
    config = read_json(path, "review conductor config")
    require_exact_keys(
        config,
        {
            "schema",
            "repository",
            "repository_id",
            "default_branch",
            "ci",
            "openclaw",
            "clawsweeper",
            "max_repair_cycles",
            "merge_policy",
        },
        {"review_policy"},
        "review conductor config",
    )
    if config["schema"] != CONFIG_SCHEMA:
        raise ContractError("review conductor config schema is unsupported")
    repository = require_text(config["repository"], "config repository", 200)
    if not REPOSITORY_RE.fullmatch(repository):
        raise ContractError("config repository must be owner/name")
    require_positive_int(config["repository_id"], "config repository_id")
    require_text(config["default_branch"], "config default_branch", 200)
    ci = require_object(config["ci"], "config ci")
    require_exact_keys(ci, {"workflow_name", "workflow_path"}, set(), "config ci")
    require_text(ci["workflow_name"], "config ci workflow_name", 200)
    require_text(ci["workflow_path"], "config ci workflow_path", 300)
    openclaw = require_object(config["openclaw"], "config openclaw")
    require_exact_keys(
        openclaw,
        {"operator_id", "remote_worktree_shelf"},
        {"transport", "exact_tuple_contract"},
        "config openclaw",
    )
    operator_id = require_text(openclaw["operator_id"], "config openclaw operator_id", 128)
    if not SAFE_ID_RE.fullmatch(operator_id) or ":" in operator_id:
        raise ContractError("config openclaw operator_id is unsafe")
    shelf = require_text(openclaw["remote_worktree_shelf"], "config openclaw remote_worktree_shelf", 500)
    if not shelf.startswith("/") or ".." in Path(shelf).parts:
        raise ContractError("config openclaw remote_worktree_shelf must be an absolute safe path")
    openclaw.setdefault("transport", "origin")
    if openclaw["transport"] not in {"bundle", "origin"}:
        raise ContractError("config openclaw transport must be bundle or origin")
    if "exact_tuple_contract" in openclaw:
        declared = require_text(
            openclaw["exact_tuple_contract"], "config openclaw exact_tuple_contract", 64
        )
        if declared != OPENCLAW_EXACT_TUPLE_CONTRACT:
            raise ContractError(
                "config openclaw exact_tuple_contract must be the pinned companion contract"
            )
    clawsweeper = require_object(config["clawsweeper"], "config clawsweeper")
    require_exact_keys(
        clawsweeper,
        {"workflow_id", "ref", "publish"},
        {"workflow_name", "workflow_path"},
        "config clawsweeper",
    )
    require_text(clawsweeper["workflow_id"], "config clawsweeper workflow_id", 200)
    has_workflow_name = "workflow_name" in clawsweeper
    has_workflow_path = "workflow_path" in clawsweeper
    if has_workflow_name != has_workflow_path:
        raise ContractError(
            "config clawsweeper workflow_name and workflow_path must be supplied together"
        )
    if has_workflow_name:
        require_text(clawsweeper["workflow_name"], "config clawsweeper workflow_name", 200)
        require_text(clawsweeper["workflow_path"], "config clawsweeper workflow_path", 300)
    else:
        clawsweeper["workflow_name"] = None
        clawsweeper["workflow_path"] = None
    require_text(clawsweeper["ref"], "config clawsweeper ref", 200)
    if clawsweeper["publish"] is not True:
        raise ContractError("config clawsweeper publish must be true for the pilot")
    cycles = require_positive_int(config["max_repair_cycles"], "config max_repair_cycles")
    if cycles != 2:
        raise ContractError("config max_repair_cycles must remain exactly 2")
    if config["merge_policy"] != "human_only":
        raise ContractError("config merge_policy must remain human_only")
    if "review_policy" in config:
        policy = require_object(config["review_policy"], "review_policy")
        if re.fullmatch(r"[A-Za-z0-9_.-]+\.ya?ml", clawsweeper["workflow_id"]) is None or clawsweeper["workflow_path"] != f".github/workflows/{clawsweeper['workflow_id']}":
            raise ContractError("profile ClawSweeper workflow must be one exact workflow file")
        require_exact_keys(policy, {"enabled", "clawsweeper_requires_ready", "comprehensive", "reviewers"}, set(), "review_policy")
        if type(policy["enabled"]) is not bool or policy["clawsweeper_requires_ready"] is not True or policy["comprehensive"] is not True:
            raise ContractError("generalized profiles require comprehensive review and ready state before ClawSweeper")
        require_exact_keys(require_object(policy["reviewers"], "reviewers"), {"openclaw", "clawsweeper"}, set(), "reviewers")
        for actor in policy["reviewers"].values():
            if actor is not None or policy["enabled"]:
                require_text(actor, "authoritative reviewer identity", 200)
        if policy["enabled"] and len(set(policy["reviewers"].values())) != 2:
            raise ContractError("reviewer identities must be distinct")
    return config


def require_enabled(config: dict[str, Any]) -> None:
    if config.get("review_policy", {}).get("enabled", True) is not True:
        raise ContractError("repository profile is inactive; enrollment prerequisites unresolved")


def ensure_state_root(path: Path) -> Path:
    if not path.is_absolute():
        raise ContractError("state root must be absolute")
    if path.exists() and path.is_symlink():
        raise ContractError("state root may not be a symlink")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    resolved = path.resolve(strict=True)
    if resolved.is_symlink():
        raise ContractError("resolved state root may not be a symlink")
    stat = resolved.stat()
    if stat.st_uid != os.getuid():
        raise ContractError("state root must be owned by the current user")
    if stat.st_mode & 0o022:
        raise ContractError("state root may not be group/world writable")
    return resolved


def open_database(state_root: Path, repository: str | None = None) -> sqlite3.Connection:
    root = ensure_state_root(state_root)
    database = root / "review-conductor.sqlite3"
    old_umask = os.umask(0o077)
    try:
        connection = sqlite3.connect(database, timeout=10)
    finally:
        os.umask(old_umask)
    connection.row_factory = sqlite3.Row
    if repository is not None:
        # Bind before migrations or recovery can touch any existing state.
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("CREATE TABLE IF NOT EXISTS repository_binding (singleton INTEGER PRIMARY KEY CHECK(singleton=1), repository TEXT NOT NULL)")
            bound = connection.execute("SELECT repository FROM repository_binding WHERE singleton=1").fetchone()
            if bound is not None and bound[0] != repository:
                raise ContractError("state root belongs to another repository")
            if connection.execute("SELECT 1 FROM sqlite_master WHERE name='heads'").fetchone():
                if connection.execute("SELECT 1 FROM heads WHERE repository != ? LIMIT 1", (repository,)).fetchone():
                    raise ContractError("state root contains another repository")
            connection.execute("INSERT OR IGNORE INTO repository_binding VALUES (1, ?)", (repository,))
            connection.commit()
        except Exception:
            connection.rollback()
            connection.close()
            raise
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS deliveries (
          delivery_id TEXT PRIMARY KEY,
          source TEXT NOT NULL,
          event_type TEXT NOT NULL,
          payload_sha256 TEXT NOT NULL,
          received_at TEXT NOT NULL,
          outcome_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS heads (
          repository TEXT NOT NULL,
          pr_number INTEGER NOT NULL,
          base_sha TEXT NOT NULL,
          head_sha TEXT NOT NULL,
          source_updated_at TEXT NOT NULL,
          head_started_at TEXT NOT NULL,
          is_current INTEGER NOT NULL CHECK (is_current IN (0, 1)),
          state TEXT NOT NULL,
          author TEXT NOT NULL,
          mutation_owner TEXT NOT NULL,
          repair_cycle INTEGER NOT NULL CHECK (repair_cycle >= 0),
          review_epoch INTEGER NOT NULL DEFAULT 0 CHECK (review_epoch >= 0),
          ci_conclusion TEXT,
          rail TEXT,
          review_request_id TEXT,
          reviewer_actor TEXT,
          repair_owner TEXT,
          blocker TEXT,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          PRIMARY KEY (repository, pr_number, base_sha, head_sha)
        );
        CREATE UNIQUE INDEX IF NOT EXISTS heads_one_current
          ON heads(repository, pr_number) WHERE is_current = 1;
        CREATE TABLE IF NOT EXISTS events (
          sequence INTEGER PRIMARY KEY AUTOINCREMENT,
          event_id TEXT NOT NULL UNIQUE,
          kind TEXT NOT NULL,
          repository TEXT NOT NULL,
          pr_number INTEGER NOT NULL,
          base_sha TEXT NOT NULL,
          head_sha TEXT NOT NULL,
          stale INTEGER NOT NULL CHECK (stale IN (0, 1)),
          payload_json TEXT NOT NULL,
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS actions (
          action_id TEXT PRIMARY KEY,
          idempotency_key TEXT NOT NULL UNIQUE,
          kind TEXT NOT NULL,
          repository TEXT NOT NULL,
          pr_number INTEGER NOT NULL,
          base_sha TEXT NOT NULL,
          head_sha TEXT NOT NULL,
          review_epoch INTEGER NOT NULL DEFAULT 0 CHECK (review_epoch >= 0),
          status TEXT NOT NULL,
          payload_json TEXT NOT NULL,
          receipt_json TEXT,
          attempts INTEGER NOT NULL DEFAULT 0,
          claim_owner TEXT,
          claimed_at TEXT,
          lease_expires_at TEXT,
          last_error TEXT,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS rail_workflow_runs (
          rail TEXT NOT NULL,
          workflow_run_id TEXT NOT NULL,
          event_id TEXT NOT NULL UNIQUE,
          workflow_name TEXT NOT NULL,
          workflow_path TEXT NOT NULL,
          workflow_head_sha TEXT NOT NULL,
          conclusion TEXT NOT NULL,
          status TEXT NOT NULL,
          source_created_at TEXT NOT NULL,
          received_at TEXT NOT NULL,
          bound_repository TEXT,
          bound_pr_number INTEGER,
          bound_base_sha TEXT,
          bound_head_sha TEXT,
          bound_review_epoch INTEGER,
          verdict TEXT,
          proof_ref TEXT,
          PRIMARY KEY (rail, workflow_run_id)
        );
        CREATE TABLE IF NOT EXISTS ci_run_identities (
          repository TEXT NOT NULL,
          workflow_run_id TEXT NOT NULL,
          pr_number INTEGER NOT NULL,
          base_sha TEXT NOT NULL,
          head_sha TEXT NOT NULL,
          conclusion TEXT NOT NULL,
          source_created_at TEXT NOT NULL,
          disposition TEXT NOT NULL,
          PRIMARY KEY (repository, workflow_run_id)
        );
        CREATE TABLE IF NOT EXISTS ci_run_identity_migrations (
          singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
          backfilled_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS projections (
          repository TEXT NOT NULL,
          pr_number INTEGER NOT NULL,
          base_sha TEXT NOT NULL,
          head_sha TEXT NOT NULL,
          review_epoch INTEGER NOT NULL,
          openclaw_check_run_id INTEGER,
          clawsweeper_check_run_id INTEGER,
          openclaw_check_create_state TEXT NOT NULL DEFAULT 'pending',
          clawsweeper_check_create_state TEXT NOT NULL DEFAULT 'pending',
          ready_label_applied INTEGER NOT NULL DEFAULT 0 CHECK (ready_label_applied IN (0, 1)),
          ready_label_reconcile_action TEXT,
          ready_label_reconciled_at TEXT,
          last_projected_state TEXT,
          last_error TEXT,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          PRIMARY KEY (repository, pr_number, base_sha, head_sha, review_epoch)
        );
        """
    )
    head_columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(heads)").fetchall()
    }
    if "source_updated_at" not in head_columns:
        connection.execute(
            "ALTER TABLE heads ADD COLUMN source_updated_at TEXT NOT NULL DEFAULT ''"
        )
    if "head_started_at" not in head_columns:
        connection.execute(
            "ALTER TABLE heads ADD COLUMN head_started_at TEXT NOT NULL DEFAULT ''"
        )
        connection.execute(
            "UPDATE heads SET head_started_at = source_updated_at WHERE head_started_at = ''"
        )
    if "review_epoch" not in head_columns:
        connection.execute(
            "ALTER TABLE heads ADD COLUMN review_epoch INTEGER NOT NULL DEFAULT 0"
        )
    action_columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(actions)").fetchall()
    }
    if "review_epoch" not in action_columns:
        connection.execute(
            "ALTER TABLE actions ADD COLUMN review_epoch INTEGER NOT NULL DEFAULT 0"
        )
    for column in ("claim_owner", "claimed_at", "lease_expires_at"):
        if column not in action_columns:
            connection.execute(f"ALTER TABLE actions ADD COLUMN {column} TEXT")
    projection_columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(projections)").fetchall()
    }
    for column in ("openclaw_check_create_state", "clawsweeper_check_create_state"):
        if column not in projection_columns:
            connection.execute(
                f"ALTER TABLE projections ADD COLUMN {column} TEXT NOT NULL DEFAULT 'pending'"
            )
    for column in ("ready_label_reconcile_action", "ready_label_reconciled_at"):
        if column not in projection_columns:
            connection.execute(f"ALTER TABLE projections ADD COLUMN {column} TEXT")
    connection.execute(
        """
        UPDATE projections SET openclaw_check_create_state = 'active'
        WHERE openclaw_check_run_id IS NOT NULL
        """
    )
    connection.execute(
        """
        UPDATE projections SET clawsweeper_check_create_state = 'active'
        WHERE clawsweeper_check_run_id IS NOT NULL
        """
    )
    for action in connection.execute(
        """
        SELECT action_id, kind, repository, pr_number, base_sha, head_sha,
               review_epoch, status, payload_json
        FROM actions
        """
    ).fetchall():
        payload = json.loads(action["payload_json"])
        if "review_epoch" not in payload:
            payload["review_epoch"] = int(action["review_epoch"])
            connection.execute(
                "UPDATE actions SET payload_json = ? WHERE action_id = ?",
                (canonical_json(payload), action["action_id"]),
            )
        identity_suffix = action_identity_suffix(action["kind"], payload)
        expected_action_id, _expected_key = action_identity(
            action["kind"],
            action["repository"],
            action["pr_number"],
            action["base_sha"],
            action["head_sha"],
            review_action_suffix(int(action["review_epoch"]), identity_suffix),
        )
        if (
            action["action_id"] != expected_action_id
            and action["status"] in {"pending", "failed", "dispatching"}
        ):
            connection.execute(
                """
                UPDATE actions
                SET status = 'obsolete',
                    last_error = 'legacy action identity is not bound to review_epoch',
                    updated_at = ?
                WHERE action_id = ? AND status IN ('pending', 'failed', 'dispatching')
                """,
                (utc_now(), action["action_id"]),
            )
    if "is_draft" not in {row[1] for row in connection.execute("PRAGMA table_info(heads)")}:
        connection.execute("ALTER TABLE heads ADD COLUMN is_draft INTEGER NOT NULL DEFAULT 0")
    if connection.execute(
        "SELECT 1 FROM ci_run_identity_migrations WHERE singleton = 1"
    ).fetchone() is None:
        for event_row in connection.execute(
            """
            SELECT repository, pr_number, base_sha, head_sha, payload_json
            FROM events
            WHERE kind = 'ci.completed'
            """
        ).fetchall():
            payload = json.loads(event_row["payload_json"])
            workflow_run = payload.get("workflow_run")
            if not isinstance(workflow_run, dict):
                continue
            try:
                workflow_run_id = str(require_positive_int(workflow_run.get("id"), "workflow_run id"))
                source_created_at = require_github_timestamp(
                    workflow_run.get("created_at"), "workflow_run created_at"
                )
                conclusion = require_text(
                    workflow_run.get("conclusion"), "workflow_run conclusion", 50
                )
            except ContractError:
                continue
            identity = (
                event_row["repository"], workflow_run_id, event_row["pr_number"],
                event_row["base_sha"], event_row["head_sha"], conclusion, source_created_at,
            )
            prior = connection.execute(
                """
                SELECT pr_number, base_sha, head_sha, conclusion, source_created_at
                FROM ci_run_identities
                WHERE repository = ? AND workflow_run_id = ?
                """,
                identity[:2],
            ).fetchone()
            if prior is not None and tuple(prior) != identity[2:]:
                raise ContractError("legacy CI workflow_run identities conflict")
            connection.execute(
                """
                INSERT OR IGNORE INTO ci_run_identities(
                  repository, workflow_run_id, pr_number, base_sha, head_sha,
                  conclusion, source_created_at, disposition
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'legacy_event')
                """,
                identity,
            )
        connection.execute(
            "INSERT OR IGNORE INTO ci_run_identity_migrations(singleton, backfilled_at) VALUES (1, ?)",
            (utc_now(),),
        )
    connection.commit()
    return connection


def row_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def current_head(connection: sqlite3.Connection, repository: str, pr_number: int) -> sqlite3.Row | None:
    return connection.execute(
        "SELECT * FROM heads WHERE repository = ? AND pr_number = ? AND is_current = 1",
        (repository, pr_number),
    ).fetchone()


def exact_current_head(
    connection: sqlite3.Connection,
    repository: str,
    pr_number: int,
    base_sha: str,
    head_sha: str,
) -> sqlite3.Row | None:
    row = current_head(connection, repository, pr_number)
    if row is None or row["base_sha"] != base_sha or row["head_sha"] != head_sha:
        return None
    return row


def event_id_from_delivery(delivery_id: str) -> str:
    return f"github:{delivery_id}"


def insert_event(
    connection: sqlite3.Connection,
    *,
    event_id: str,
    kind: str,
    repository: str,
    pr_number: int,
    base_sha: str,
    head_sha: str,
    stale: bool,
    payload: dict[str, Any],
) -> None:
    connection.execute(
        """
        INSERT INTO events(event_id, kind, repository, pr_number, base_sha, head_sha, stale, payload_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id,
            kind,
            repository,
            pr_number,
            base_sha,
            head_sha,
            int(stale),
            canonical_json(payload),
            utc_now(),
        ),
    )


def action_identity(kind: str, repository: str, pr_number: int, base_sha: str, head_sha: str, suffix: str = "") -> tuple[str, str]:
    key = f"{kind}|{repository}|{pr_number}|{base_sha}|{head_sha}|{suffix}"
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return f"act-{digest[:32]}", f"sha256:{digest}"


def review_action_suffix(review_epoch: int, suffix: str = "") -> str:
    parts = [f"review-epoch:{review_epoch}"]
    if suffix:
        parts.append(suffix)
    return "|".join(parts)


def action_identity_suffix(kind: str, payload: dict[str, Any]) -> str:
    if kind == "repair.route":
        return f"{payload.get('source_rail', '')}:{payload.get('review_request_id', '')}"
    if kind == "clawsweeper.dispatch" and payload.get("rereview_attempt"):
        return f"rereview:{payload['rereview_attempt']}"
    if kind == "rereview.acknowledge" and payload.get("trigger_comment_id"):
        return f"comment:{payload['trigger_comment_id']}"
    return ""


def insert_action(
    connection: sqlite3.Connection,
    *,
    kind: str,
    repository: str,
    pr_number: int,
    base_sha: str,
    head_sha: str,
    payload: dict[str, Any],
    suffix: str = "",
    review_epoch: int = 0,
) -> tuple[str, bool]:
    action_id, key = action_identity(
        kind,
        repository,
        pr_number,
        base_sha,
        head_sha,
        review_action_suffix(review_epoch, suffix),
    )
    now = utc_now()
    encoded = canonical_json(payload)
    existing = connection.execute(
        "SELECT payload_json FROM actions WHERE idempotency_key = ?", (key,)
    ).fetchone()
    if existing is not None:
        if existing["payload_json"] != encoded:
            raise ContractError("existing action idempotency key has different payload")
        return action_id, False
    connection.execute(
        """
        INSERT INTO actions(action_id, idempotency_key, kind, repository, pr_number, base_sha, head_sha,
                            review_epoch, status, payload_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)
        """,
        (
            action_id,
            key,
            kind,
            repository,
            pr_number,
            base_sha,
            head_sha,
            review_epoch,
            encoded,
            now,
            now,
        ),
    )
    return action_id, True


def remote_worktree(config: dict[str, Any], pr_number: int, head_sha: str) -> str:
    repository_slug = config["repository"].replace("/", "-").lower()
    child = f"review-conductor-{repository_slug}-pr{pr_number}-{head_sha[:12]}"
    return str(Path(config["openclaw"]["remote_worktree_shelf"]) / child)


def openclaw_action_payload(
    config: dict[str, Any],
    pr_number: int,
    base_sha: str,
    head_sha: str,
    review_epoch: int,
) -> dict[str, Any]:
    action_id, _ = action_identity(
        "openclaw.enqueue",
        config["repository"],
        pr_number,
        base_sha,
        head_sha,
        review_action_suffix(review_epoch),
    )
    return {
        "schema": "smoky.review-conductor.action.v1",
        "kind": "openclaw.enqueue",
        "repository": config["repository"],
        "pr_number": pr_number,
        "base_sha": base_sha,
        "head_sha": head_sha,
        "review_epoch": review_epoch,
        "pr_url": f"https://github.com/{config['repository']}/pull/{pr_number}",
        "operator_id": config["openclaw"]["operator_id"],
        "transport": config["openclaw"]["transport"],
        "queue_request_id": f"rc-{action_id[4:]}",
        "remote_worktree": remote_worktree(config, pr_number, head_sha),
        "merge_authorized": False,
    }


def clawsweeper_action_payload(
    config: dict[str, Any],
    pr_number: int,
    base_sha: str,
    head_sha: str,
    review_epoch: int,
) -> dict[str, Any]:
    return {
        "schema": "smoky.review-conductor.action.v1",
        "kind": "clawsweeper.dispatch",
        "repository": config["repository"],
        "pr_number": pr_number,
        "base_sha": base_sha,
        "head_sha": head_sha,
        "review_epoch": review_epoch,
        "workflow_id": config["clawsweeper"]["workflow_id"],
        "ref": config["clawsweeper"]["ref"],
        "publish": True,
        "merge_authorized": False,
    }


def parse_rereview_command(body: str) -> str | None:
    match = REREVIEW_COMMAND_RE.search(body)
    if match is None:
        return None
    return match.group(1).lower()


def current_clawsweeper_dispatch(
    connection: sqlite3.Connection,
    identity: dict[str, Any],
    review_epoch: int,
) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT * FROM actions
        WHERE repository = ? AND pr_number = ? AND base_sha = ? AND head_sha = ?
          AND kind = 'clawsweeper.dispatch' AND review_epoch = ?
          AND status IN ('pending', 'preparing', 'dispatching', 'dispatched')
        ORDER BY created_at DESC, rowid DESC
        LIMIT 1
        """,
        (*identity.values(), review_epoch),
    ).fetchone()


def next_rereview_attempt(
    connection: sqlite3.Connection,
    identity: dict[str, Any],
    review_epoch: int,
) -> int:
    count = connection.execute(
        """
        SELECT COUNT(*) FROM actions
        WHERE repository = ? AND pr_number = ? AND base_sha = ? AND head_sha = ?
          AND kind = 'clawsweeper.dispatch' AND review_epoch = ?
        """,
        (*identity.values(), review_epoch),
    ).fetchone()[0]
    return int(count) + 1


def comment_already_processed(
    connection: sqlite3.Connection,
    repository: str,
    pr_number: int,
    comment_id: int,
) -> bool:
    for row in connection.execute(
        """
        SELECT payload_json FROM events
        WHERE kind = 'issue_comment.created' AND repository = ? AND pr_number = ?
        """,
        (repository, pr_number),
    ).fetchall():
        payload = json.loads(row["payload_json"])
        if int(payload.get("comment_id") or 0) == comment_id:
            return True
    return False


def same_head_prerequisites_reason(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
    identity: dict[str, Any],
) -> str | None:
    if row["ci_conclusion"] != "success":
        return "current-tuple CI is not a successful comprehensive gate"
    for event in connection.execute(
        """
        SELECT payload_json FROM events
        WHERE kind = 'openclaw.terminal' AND stale = 0
          AND repository = ? AND pr_number = ? AND base_sha = ? AND head_sha = ?
        ORDER BY sequence DESC
        """,
        (*identity.values(),),
    ).fetchall():
        payload = json.loads(event["payload_json"])
        if payload.get("result") != "clean":
            continue
        epoch = payload.get("review_epoch")
        if epoch is not None and int(epoch) != int(row["review_epoch"]):
            continue
        return None
    return "current-tuple comprehensive OpenClaw is not clean"


def rereview_sender_refusal(config: dict[str, Any], event: dict[str, Any]) -> str | None:
    login = event["sender"]
    if event["sender_type"] != "User":
        return "requester is not a human user"
    if login.lower().endswith("[bot]"):
        return "requester is a reviewer or bot"
    reviewers = (config.get("review_policy") or {}).get("reviewers") or {}
    blocked = {str(actor).lower() for actor in reviewers.values() if actor}
    if login.lower() in blocked:
        return "requester is a reviewer or bot"
    if event["author_association"] not in MAINTAINER_ASSOCIATIONS:
        return "requester is not an authoritative maintainer"
    return None


def rereview_state_refusal(config: dict[str, Any], row: sqlite3.Row) -> tuple[str, str] | None:
    state = row["state"]
    if state in REREVIEW_CLOSED_STATES:
        return "refused", f"pull request is {state}"
    if state in REREVIEW_IN_FLIGHT_STATES:
        return "waiting", "ClawSweeper review is already in flight for this exact tuple"
    if config.get("review_policy") and bool(row["is_draft"]):
        return "waiting", "ClawSweeper waits for ready-for-review state"
    if (
        state == "waiting_human"
        and row["blocker"] == "two automatic repair cycles exhausted"
    ) or (
        state == "waiting_human"
        and int(row["repair_cycle"]) >= int(config["max_repair_cycles"])
    ):
        return "refused", "two automatic repair cycles exhausted"
    if state in REREVIEW_PREREQUISITE_STATES:
        return "waiting", "current-tuple CI and comprehensive OpenClaw must clear before ClawSweeper"
    return None


def acknowledgement_body(
    *,
    decision: str,
    repository: str,
    pr_number: int,
    head_sha: str,
    review_epoch: int,
    attempt: int | None,
    reason: str | None,
    command: str,
) -> str:
    target = f"{repository}#{pr_number} head {head_sha[:12]} epoch {review_epoch}"
    if attempt is not None:
        target = f"{target} attempt {attempt}"
    if decision == "accepted":
        return (
            f"Review Conductor accepted @{command} for {target}. "
            "Fresh ClawSweeper evidence will be collected for this exact tuple. "
            "Merge remains human-only."
        )
    if decision == "waiting":
        return f"Review Conductor is waiting to start @{command} for {target}: {reason}."
    return f"Review Conductor refused @{command} for {target}: {reason}."


def insert_rereview_acknowledgement(
    connection: sqlite3.Connection,
    *,
    identity: dict[str, Any],
    review_epoch: int,
    comment_id: int,
    decision: str,
    body: str,
    attempt: int | None,
    command: str,
) -> tuple[str, bool]:
    payload = {
        "schema": "smoky.review-conductor.action.v1",
        "kind": "rereview.acknowledge",
        **identity,
        "review_epoch": review_epoch,
        "trigger_comment_id": comment_id,
        "decision": decision,
        "command": command,
        "body": body,
        "rereview_attempt": attempt,
        "merge_authorized": False,
    }
    return insert_action(
        connection,
        kind="rereview.acknowledge",
        payload=payload,
        suffix=f"comment:{comment_id}",
        review_epoch=review_epoch,
        **identity,
    )


def queue_clawsweeper_rereview(
    connection: sqlite3.Connection,
    config: dict[str, Any],
    row: sqlite3.Row,
    identity: dict[str, Any],
    event: dict[str, Any],
) -> tuple[str, int, bool]:
    review_epoch = int(row["review_epoch"])
    previous = current_clawsweeper_dispatch(connection, identity, review_epoch)
    previous_run = row["review_request_id"]
    if previous is not None and previous["receipt_json"]:
        receipt = json.loads(previous["receipt_json"])
        previous_run = previous_run or receipt.get("workflow_run_id")
    connection.execute(
        """
        UPDATE actions
        SET status = 'obsolete', last_error = 'superseded by maintainer rereview',
            claim_owner = NULL, claimed_at = NULL, lease_expires_at = NULL, updated_at = ?
        WHERE repository = ? AND pr_number = ? AND base_sha = ? AND head_sha = ?
          AND review_epoch = ? AND kind = 'clawsweeper.dispatch'
          AND status IN ('pending', 'failed', 'dispatching', 'preparing', 'dispatched')
        """,
        (utc_now(), *identity.values(), review_epoch),
    )
    attempt = next_rereview_attempt(connection, identity, review_epoch)
    payload = clawsweeper_action_payload(
        config,
        identity["pr_number"],
        identity["base_sha"],
        identity["head_sha"],
        review_epoch,
    )
    payload.update(
        {
            "trigger": "maintainer_rereview",
            "rereview_attempt": attempt,
            "trigger_comment_id": event["comment_id"],
            "evidence_refreshed": True,
            "evidence_input_sha256": event["evidence_input_sha256"],
            "previous_workflow_run_id": str(previous_run) if previous_run else None,
        }
    )
    action_id, created = insert_action(
        connection,
        kind="clawsweeper.dispatch",
        payload=payload,
        suffix=f"rereview:{attempt}",
        review_epoch=review_epoch,
        **identity,
    )
    update_exact_head(
        connection,
        identity,
        state="clawsweeper_queued",
        rail="clawsweeper",
        review_request_id=None,
        blocker=None,
    )
    return action_id, attempt, created


def clawsweeper_attempt_mismatch(
    dispatch: sqlite3.Row,
    workflow_run_id: str,
) -> str | None:
    payload = json.loads(dispatch["payload_json"])
    previous = payload.get("previous_workflow_run_id")
    if previous is not None and str(previous) == workflow_run_id:
        return "ClawSweeper completion is not the current attempt"
    return None


def begin_new_head(
    connection: sqlite3.Connection,
    *,
    repository: str,
    pr_number: int,
    base_sha: str,
    head_sha: str,
    author: str,
    source_updated_at: str,
) -> tuple[sqlite3.Row, bool]:
    previous = current_head(connection, repository, pr_number)
    if previous is not None and previous["base_sha"] == base_sha and previous["head_sha"] == head_sha:
        if source_updated_at > previous["source_updated_at"]:
            connection.execute(
                """
                UPDATE heads SET source_updated_at = ?, updated_at = ?
                WHERE repository = ? AND pr_number = ? AND base_sha = ? AND head_sha = ?
                """,
                (
                    source_updated_at,
                    utc_now(),
                    repository,
                    pr_number,
                    base_sha,
                    head_sha,
                ),
            )
            refreshed = exact_current_head(
                connection, repository, pr_number, base_sha, head_sha
            )
            if refreshed is None:
                raise ContractError("failed to refresh exact-head event freshness")
            return refreshed, False
        return previous, False
    target = connection.execute(
        """
        SELECT review_epoch FROM heads
        WHERE repository = ? AND pr_number = ? AND base_sha = ? AND head_sha = ?
        """,
        (repository, pr_number, base_sha, head_sha),
    ).fetchone()
    review_epoch = int(target["review_epoch"]) + 1 if target is not None else 0
    repair_cycle = 0
    mutation_owner = author
    if previous is not None:
        repair_cycle = int(previous["repair_cycle"])
        mutation_owner = previous["mutation_owner"]
        if previous["state"] == "repair_required":
            # Saturating 2/2 ledger: later scoped required_fix heads do not
            # open a third broad automatic round or reset the count.
            if repair_cycle < 2:
                repair_cycle += 1
            mutation_owner = previous["repair_owner"] or mutation_owner
        connection.execute(
            "UPDATE heads SET is_current = 0, updated_at = ? WHERE repository = ? AND pr_number = ? AND is_current = 1",
            (utc_now(), repository, pr_number),
        )
        connection.execute(
            """
            UPDATE actions
            SET status = 'obsolete',
                last_error = 'superseded by a new pull request incarnation',
                claim_owner = NULL,
                claimed_at = NULL,
                lease_expires_at = NULL,
                updated_at = ?
            WHERE repository = ? AND pr_number = ?
              AND base_sha = ? AND head_sha = ? AND review_epoch = ?
              AND status IN ('pending', 'failed', 'dispatching')
            """,
            (
                utc_now(),
                repository,
                pr_number,
                previous["base_sha"],
                previous["head_sha"],
                previous["review_epoch"],
            ),
        )
    now = utc_now()
    connection.execute(
        """
        INSERT INTO heads(repository, pr_number, base_sha, head_sha, source_updated_at, head_started_at, is_current, state, author,
                          mutation_owner, repair_cycle, review_epoch, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, 1, 'ci_running', ?, ?, ?, ?, ?, ?)
        ON CONFLICT(repository, pr_number, base_sha, head_sha) DO UPDATE SET
          is_current = 1,
          state = 'ci_running',
          source_updated_at = excluded.source_updated_at,
          head_started_at = excluded.head_started_at,
          author = excluded.author,
          mutation_owner = excluded.mutation_owner,
          repair_cycle = excluded.repair_cycle,
          review_epoch = excluded.review_epoch,
          ci_conclusion = NULL,
          rail = NULL,
          review_request_id = NULL,
          reviewer_actor = NULL,
          repair_owner = NULL,
          blocker = NULL,
          updated_at = excluded.updated_at
        """,
        (
            repository,
            pr_number,
            base_sha,
            head_sha,
            source_updated_at,
            source_updated_at,
            author,
            mutation_owner,
            repair_cycle,
            review_epoch,
            now,
            now,
        ),
    )
    row = exact_current_head(connection, repository, pr_number, base_sha, head_sha)
    if row is None:
        raise ContractError("failed to project new exact head")
    return row, True


def parse_pull_request_event(config: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    repository = require_object(payload.get("repository"), "payload repository")
    if repository.get("full_name") != config["repository"] or repository.get("id") != config["repository_id"]:
        raise ContractError("GitHub repository identity does not match config")
    action = require_text(payload.get("action"), "pull_request action", 50)
    if action not in {"opened", "reopened", "synchronize", "closed", "ready_for_review", "converted_to_draft"}:
        raise ContractError(f"unsupported pull_request action {action}")
    pull = require_object(payload.get("pull_request"), "payload pull_request")
    if config.get("review_policy") and type(pull.get("draft")) is not bool:
        raise ContractError("profile requires authoritative pull_request draft status")
    pr_number = require_positive_int(pull.get("number"), "pull_request number")
    base = require_object(pull.get("base"), "pull_request base")
    head = require_object(pull.get("head"), "pull_request head")
    user = require_object(pull.get("user"), "pull_request user")
    base_ref = require_text(base.get("ref"), "pull_request base ref", 200)
    if base_ref != config["default_branch"]:
        return {"ignored": f"pull_request base ref {base_ref} is outside the configured pilot"}
    return {
        "action": action,
        "repository": config["repository"],
        "pr_number": pr_number,
        "base_sha": require_sha(base.get("sha"), "pull_request base sha"),
        "head_sha": require_sha(head.get("sha"), "pull_request head sha"),
        "author": require_text(user.get("login"), "pull_request author", 200),
        "source_updated_at": require_github_timestamp(
            pull.get("updated_at"), "pull_request updated_at"
        ),
        "merged": bool(pull.get("merged", False)),
        "is_draft": pull.get("draft", False),
    }


def parse_workflow_run_event(config: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    repository = require_object(payload.get("repository"), "payload repository")
    if repository.get("full_name") != config["repository"] or repository.get("id") != config["repository_id"]:
        raise ContractError("GitHub repository identity does not match config")
    if payload.get("action") != "completed":
        raise ContractError("workflow_run action must be completed")
    workflow = require_object(payload.get("workflow"), "payload workflow")
    run = require_object(payload.get("workflow_run"), "payload workflow_run")
    if (
        workflow.get("name") == config["clawsweeper"]["workflow_name"]
        and workflow.get("path") == config["clawsweeper"]["workflow_path"]
    ):
        workflow_run_id = require_positive_int(run.get("id"), "ClawSweeper workflow_run id")
        if run.get("event") != "workflow_dispatch" or run.get("status") != "completed":
            return {
                "ignored": "ClawSweeper workflow_run is not terminal workflow_dispatch",
                "rail": "clawsweeper",
                "workflow_run_id": workflow_run_id,
            }
        return {
            "rail": "clawsweeper",
            "workflow_run_id": str(workflow_run_id),
            "workflow_name": config["clawsweeper"]["workflow_name"],
            "workflow_path": config["clawsweeper"]["workflow_path"],
            "workflow_head_sha": require_sha(
                run.get("head_sha"), "ClawSweeper workflow_run head_sha"
            ),
            "conclusion": require_text(
                run.get("conclusion"), "ClawSweeper workflow_run conclusion", 50
            ),
            "source_created_at": require_github_timestamp(
                run.get("created_at"), "ClawSweeper workflow_run created_at"
            ),
        }
    if workflow.get("name") != config["ci"]["workflow_name"] or workflow.get("path") != config["ci"]["workflow_path"]:
        raise ContractError("workflow_run is not an allowlisted Review Conductor workflow")
    if run.get("event") != "pull_request" or run.get("status") != "completed":
        raise ContractError("CI workflow_run must be terminal pull_request CI")
    pulls = run.get("pull_requests")
    if not isinstance(pulls, list) or len(pulls) != 1:
        raise ContractError("workflow_run must identify exactly one pull request")
    pull = require_object(pulls[0], "workflow_run pull request")
    base = require_object(pull.get("base"), "workflow_run pull request base")
    head = require_object(pull.get("head"), "workflow_run pull request head")
    base_ref = require_text(base.get("ref"), "workflow_run pull request base ref", 200)
    head_sha = require_sha(head.get("sha"), "workflow_run pull request head sha")
    if require_sha(run.get("head_sha"), "workflow_run head_sha") != head_sha:
        raise ContractError("workflow_run head_sha does not match pull request head sha")
    run_id = require_positive_int(run.get("id"), "workflow_run id")
    conclusion = require_text(run.get("conclusion"), "workflow_run conclusion", 50)
    source_created_at = require_github_timestamp(
        run.get("created_at"), "workflow_run created_at"
    )
    if base_ref != config["default_branch"]:
        return {
            "ignored": f"workflow_run base ref {base_ref} is outside the configured pilot",
            "repository": config["repository"],
            "workflow_run_id": run_id,
            "pr_number": require_positive_int(
                pull.get("number"), "workflow_run pull request number"
            ),
            "base_sha": require_sha(base.get("sha"), "workflow_run pull request base sha"),
            "head_sha": head_sha,
            "conclusion": conclusion,
            "source_created_at": source_created_at,
        }
    return {
        "repository": config["repository"],
        "pr_number": require_positive_int(pull.get("number"), "workflow_run pull request number"),
        "base_sha": require_sha(base.get("sha"), "workflow_run pull request base sha"),
        "head_sha": head_sha,
        "conclusion": conclusion,
        "run_id": run_id,
        "source_created_at": source_created_at,
    }


def _ci_run_identity(event: dict[str, Any]) -> tuple[str, str, int, str, str, str, str] | None:
    workflow_run_id = event.get("run_id", event.get("workflow_run_id"))
    fields = (
        event.get("repository"),
        workflow_run_id,
        event.get("pr_number"),
        event.get("base_sha"),
        event.get("head_sha"),
        event.get("conclusion"),
        event.get("source_created_at"),
    )
    if any(value is None for value in fields):
        return None
    return (
        str(fields[0]),
        str(workflow_run_id),
        int(fields[2]),
        str(fields[3]),
        str(fields[4]),
        str(fields[5]),
        str(fields[6]),
    )


def _remember_ci_run_identity(
    connection: sqlite3.Connection,
    event: dict[str, Any],
    disposition: str,
) -> None:
    identity = _ci_run_identity(event)
    if identity is None:
        return
    repository, workflow_run_id, pr_number, base_sha, head_sha, conclusion, source_created_at = identity
    prior = connection.execute(
        """
        SELECT pr_number, base_sha, head_sha, conclusion, source_created_at
        FROM ci_run_identities
        WHERE repository = ? AND workflow_run_id = ?
        """,
        (repository, workflow_run_id),
    ).fetchone()
    if prior is not None and (
        prior["pr_number"] != pr_number
        or prior["base_sha"] != base_sha
        or prior["head_sha"] != head_sha
        or prior["conclusion"] != conclusion
        or prior["source_created_at"] != source_created_at
    ):
        raise ContractError("CI workflow_run identity was reused with conflicting terminal facts")
    connection.execute(
        """
        INSERT INTO ci_run_identities(
          repository, workflow_run_id, pr_number, base_sha, head_sha,
          conclusion, source_created_at, disposition
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(repository, workflow_run_id) DO NOTHING
        """,
        (*identity, disposition),
    )


def _set_ci_run_disposition(
    connection: sqlite3.Connection,
    event: dict[str, Any],
    disposition: str,
) -> None:
    identity = _ci_run_identity(event)
    if identity is None:
        return
    connection.execute(
        """
        UPDATE ci_run_identities
        SET disposition = ?
        WHERE repository = ? AND workflow_run_id = ? AND disposition = 'seen'
        """,
        (disposition, identity[0], identity[1]),
    )


def parse_issue_comment_event(config: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    repository = require_object(payload.get("repository"), "payload repository")
    if repository.get("full_name") != config["repository"] or repository.get("id") != config["repository_id"]:
        raise ContractError("GitHub repository identity does not match config")
    action = require_text(payload.get("action"), "issue_comment action", 50)
    issue = require_object(payload.get("issue"), "payload issue")
    if not isinstance(issue.get("pull_request"), dict):
        return {"ignored": "issue_comment is not on a pull request"}
    if action != "created":
        return {"ignored": f"issue_comment action {action} is not a supported rereview trigger"}
    comment = require_object(payload.get("comment"), "payload comment")
    user = require_object(comment.get("user"), "issue_comment user")
    sender = payload.get("sender")
    sender_login = require_text(user.get("login"), "issue_comment user login", 200)
    sender_type = require_text(user.get("type"), "issue_comment user type", 50)
    if isinstance(sender, dict):
        if require_text(sender.get("login"), "issue_comment sender login", 200) != sender_login:
            raise ContractError("issue_comment sender does not match comment author")
        if require_text(sender.get("type"), "issue_comment sender type", 50) != sender_type:
            raise ContractError("issue_comment sender type does not match comment author")
    body = comment.get("body")
    if type(body) is not str:
        raise ContractError("issue_comment body must be a string")
    issue_body = issue.get("body") if type(issue.get("body")) is str else ""
    return {
        "action": action,
        "repository": config["repository"],
        "pr_number": require_positive_int(issue.get("number"), "issue_comment pull request number"),
        "comment_id": require_positive_int(comment.get("id"), "issue_comment id"),
        "sender": sender_login,
        "sender_type": sender_type,
        "author_association": require_text(
            comment.get("author_association"), "issue_comment author_association", 50
        ),
        "command": parse_rereview_command(body),
        "source_updated_at": require_github_timestamp(
            comment.get("created_at"), "issue_comment created_at"
        ),
        "evidence_input_sha256": hashlib.sha256(issue_body.encode("utf-8")).hexdigest(),
    }


def process_issue_comment(
    connection: sqlite3.Connection,
    config: dict[str, Any],
    event_id: str,
    event: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    require_enabled(config)
    if "ignored" in event:
        return {
            "result": "ignored",
            "reason": event["ignored"],
            "review_invoked": False,
            "merge_dispatched": False,
        }
    row = current_head(connection, event["repository"], event["pr_number"])
    if row is None:
        return {
            "result": "ignored",
            "reason": "pull request has no current exact review tuple",
            "review_invoked": False,
            "merge_dispatched": False,
        }
    identity = {
        "repository": row["repository"],
        "pr_number": int(row["pr_number"]),
        "base_sha": row["base_sha"],
        "head_sha": row["head_sha"],
    }
    if comment_already_processed(connection, identity["repository"], identity["pr_number"], event["comment_id"]):
        insert_event(
            connection,
            event_id=event_id,
            kind="issue_comment.created",
            stale=True,
            payload={**event, "duplicate_comment": True},
            **identity,
        )
        return {
            "result": "duplicate",
            "reason": "issue_comment id was already consumed",
            "state": row["state"],
            "review_epoch": row["review_epoch"],
            "merge_dispatched": False,
        }
    if event["command"] is None:
        insert_event(
            connection,
            event_id=event_id,
            kind="issue_comment.created",
            stale=False,
            payload=event,
            **identity,
        )
        return {
            "result": "ignored",
            "reason": "issue_comment is not a documented ClawSweeper rereview command",
            "state": row["state"],
            "review_epoch": row["review_epoch"],
            "merge_dispatched": False,
        }
    command = f"ClawSweeper {event['command']}"
    sender_reason = rereview_sender_refusal(config, event)
    if sender_reason is not None:
        insert_event(
            connection,
            event_id=event_id,
            kind="issue_comment.created",
            stale=False,
            payload=event,
            **identity,
        )
        return {
            "result": "refused",
            "reason": sender_reason,
            "state": row["state"],
            "review_epoch": row["review_epoch"],
            "merge_dispatched": False,
        }
    decision_reason = rereview_state_refusal(config, row)
    action_id = None
    created = False
    attempt = None
    if decision_reason is None:
        prerequisite = same_head_prerequisites_reason(connection, row, identity)
        if prerequisite is not None:
            decision_reason = ("waiting", prerequisite)
    if decision_reason is None:
        action_id, attempt, created = queue_clawsweeper_rereview(
            connection, config, row, identity, event
        )
        decision = "accepted"
        reason = None
        next_state = "clawsweeper_queued"
    else:
        decision, reason = decision_reason
        next_state = row["state"]
    ack_body = acknowledgement_body(
        decision=decision,
        repository=identity["repository"],
        pr_number=identity["pr_number"],
        head_sha=identity["head_sha"],
        review_epoch=int(row["review_epoch"]),
        attempt=attempt,
        reason=reason,
        command=command,
    )
    ack_id, ack_created = insert_rereview_acknowledgement(
        connection,
        identity=identity,
        review_epoch=int(row["review_epoch"]),
        comment_id=event["comment_id"],
        decision=decision,
        body=ack_body,
        attempt=attempt,
        command=command,
    )
    insert_event(
        connection,
        event_id=event_id,
        kind="issue_comment.created",
        stale=False,
        payload=event,
        **identity,
    )
    return {
        "result": decision,
        "reason": reason,
        "state": next_state,
        "review_epoch": row["review_epoch"],
        "rereview_attempt": attempt,
        "action_id": action_id,
        "action_created": created,
        "acknowledgement_action_id": ack_id,
        "acknowledgement_created": ack_created,
        "acknowledgement": ack_body,
        "merge_dispatched": False,
    }


def process_pull_request(
    connection: sqlite3.Connection, config: dict[str, Any], event_id: str,
    event: dict[str, Any], payload: dict[str, Any],
) -> dict[str, Any]:
    require_enabled(config)
    if not config.get("review_policy") or "ignored" in event:
        return _process_pull_request(connection, config, event_id, event, payload)
    previous = current_head(connection, event["repository"], event["pr_number"])
    original_action = event["action"]
    if previous is not None and event["source_updated_at"] <= previous["source_updated_at"] and original_action != "closed":
        insert_event(connection, event_id=event_id, kind=f"pull_request.{original_action}", stale=True, payload=payload, **{k:event[k] for k in ("repository", "pr_number", "base_sha", "head_sha")})
        return {"result": "stale", "review_invoked": False, "merge_dispatched": False}
    if (previous is not None and previous["state"] in {"closed", "closed_merged"}
        and original_action not in {"reopened", "closed"}):
        raise ContractError("only a reopened event can reopen a terminal PR")
    if original_action in {"ready_for_review", "converted_to_draft"} and previous is not None:
        identity = {k: event[k] for k in ("repository", "pr_number", "base_sha", "head_sha")}
        if previous["base_sha"] != event["base_sha"] or previous["head_sha"] != event["head_sha"]:
            result = _process_pull_request(connection, config, event_id, event, payload)
            if result["result"] in {"accepted", "duplicate"}:
                connection.execute(
                    "UPDATE heads SET is_draft=? WHERE repository=? AND pr_number=? AND base_sha=? AND head_sha=? AND is_current=1",
                    (int(event["is_draft"]), *identity.values()),
                )
            return result
        is_draft = original_action == "converted_to_draft"
        connection.execute(
            "UPDATE heads SET is_draft=?, source_updated_at=?, updated_at=? WHERE repository=? AND pr_number=? AND base_sha=? AND head_sha=? AND is_current=1",
            (int(is_draft), event["source_updated_at"], utc_now(), *identity.values()),
        )
        action_id = None
        created = False
        state = previous["state"]
        if is_draft and state == "ready_for_human_merge":
            state = "clawsweeper_clean_draft"
            update_exact_head(connection, identity, state=state, rail="clawsweeper", blocker="ready-for-human clearance is paused while the pull request is draft")
        elif is_draft and state == "clawsweeper_queued":
            paused = connection.execute(
                "UPDATE actions SET status='obsolete', last_error='pull request returned to draft', updated_at=? WHERE repository=? AND pr_number=? AND base_sha=? AND head_sha=? AND review_epoch=? AND kind='clawsweeper.dispatch' AND status IN ('pending','failed','dispatching')",
                (utc_now(), *identity.values(), previous["review_epoch"]),
            ).rowcount
            if paused == 1:
                state = "openclaw_clean_draft"
                update_exact_head(connection, identity, state=state, rail="openclaw", blocker="ClawSweeper waits for ready-for-review state")
        elif not is_draft and state == "clawsweeper_clean_draft":
            state = "ready_for_human_merge"
            update_exact_head(connection, identity, state=state, rail="clawsweeper", blocker="human merge authority required")
        elif not is_draft and state == "openclaw_clean_draft":
            payload_action = clawsweeper_action_payload(config, event["pr_number"], event["base_sha"], event["head_sha"], int(previous["review_epoch"]))
            action_id, created = insert_action(connection, kind="clawsweeper.dispatch", payload=payload_action, review_epoch=int(previous["review_epoch"]), **identity)
            if not created:
                resumed = connection.execute(
                    """
                    UPDATE actions
                    SET status='pending', last_error=NULL, claim_owner=NULL,
                        claimed_at=NULL, lease_expires_at=NULL, updated_at=?
                    WHERE action_id=? AND status='obsolete'
                      AND last_error='pull request returned to draft'
                    """,
                    (utc_now(), action_id),
                ).rowcount
                if resumed != 1:
                    action = connection.execute(
                        "SELECT status FROM actions WHERE action_id=?", (action_id,)
                    ).fetchone()
                    if action is None or action["status"] not in {"pending", "dispatching", "dispatched"}:
                        raise ContractError("ClawSweeper action cannot be resumed safely")
            state = "clawsweeper_queued"
            update_exact_head(connection, identity, state=state, rail="clawsweeper", review_request_id=None, blocker=None)
        insert_event(connection, event_id=event_id, kind=f"pull_request.{original_action}", stale=False, payload=payload, **identity)
        return {"result": "accepted", "state": state, "review_epoch": previous["review_epoch"], "action_id": action_id, "action_created": created, "merge_dispatched": False}
    elif previous is not None and bool(previous["is_draft"]) != event["is_draft"] and original_action != "closed":
        event = {**event, "action": "reopened"}
    result = _process_pull_request(connection, config, event_id, event, payload)
    if result["result"] in {"accepted", "duplicate"} and original_action != "closed":
        connection.execute("UPDATE heads SET is_draft=? WHERE repository=? AND pr_number=? AND base_sha=? AND head_sha=? AND is_current=1", (int(event["is_draft"]), *(event[k] for k in ("repository", "pr_number", "base_sha", "head_sha"))))
    return result


def _process_pull_request(
    connection: sqlite3.Connection,
    config: dict[str, Any],
    event_id: str,
    event: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    if "ignored" in event:
        return {"result": "ignored", "reason": event["ignored"]}
    identity = {
        key: event[key]
        for key in ("repository", "pr_number", "base_sha", "head_sha")
    }
    if event["action"] == "closed":
        exact = exact_current_head(connection, **identity)
        stale = exact is None or bool(
            exact["source_updated_at"]
            and event["source_updated_at"] < exact["source_updated_at"]
        )
        target_state = (
            "closed_merged"
            if event["merged"] or (exact is not None and exact["state"] == "closed_merged")
            else "closed"
        )
        duplicate = bool(
            exact is not None
            and event["source_updated_at"] == exact["source_updated_at"]
            and exact["state"] == target_state
        )
        if exact is not None and not stale and not duplicate:
            connection.execute(
                "UPDATE heads SET state = ?, source_updated_at = ?, blocker = ?, updated_at = ? WHERE repository = ? AND pr_number = ? AND base_sha = ? AND head_sha = ?",
                (
                    target_state,
                    max(event["source_updated_at"], exact["source_updated_at"]),
                    "merge was observed as historical input; conductor never performs merge"
                    if target_state == "closed_merged"
                    else None,
                    utc_now(),
                    *identity.values(),
                ),
            )
            connection.execute(
                """
                UPDATE actions
                SET status = 'obsolete', last_error = 'pull request closed',
                    claim_owner = NULL, claimed_at = NULL, lease_expires_at = NULL,
                    updated_at = ?
                WHERE repository = ? AND pr_number = ? AND base_sha = ? AND head_sha = ?
                  AND review_epoch = ?
                  AND status IN ('pending', 'failed', 'dispatching')
                """,
                (utc_now(), *identity.values(), exact["review_epoch"]),
            )
        insert_event(
            connection,
            event_id=event_id,
            kind="pull_request.closed",
            stale=stale,
            payload=payload,
            **identity,
        )
        return {
            "result": "stale" if stale else "duplicate" if duplicate else "accepted",
            "state": target_state,
            "merge_dispatched": False,
        }
    if event["action"] == "reopened":
        exact = exact_current_head(connection, **identity)
        if exact is not None:
            if exact["state"] == "closed_merged":
                insert_event(
                    connection,
                    event_id=event_id,
                    kind="pull_request.reopened",
                    stale=True,
                    payload=payload,
                    **identity,
                )
                return {
                    "result": "ignored",
                    "state": "closed_merged",
                    "reason": "a merged pull request cannot be reopened",
                    "merge_dispatched": False,
                }
            if event["source_updated_at"] <= exact["source_updated_at"]:
                duplicate = (
                    event["source_updated_at"] == exact["source_updated_at"]
                    and exact["state"] not in {"closed", "closed_merged"}
                )
                insert_event(
                    connection,
                    event_id=event_id,
                    kind="pull_request.reopened",
                    stale=not duplicate,
                    payload=payload,
                    **identity,
                )
                return {
                    "result": "duplicate" if duplicate else "stale",
                    "state": exact["state"],
                    "repair_cycle": exact["repair_cycle"],
                    "review_epoch": exact["review_epoch"],
                    "merge_dispatched": False,
                }
            next_epoch = int(exact["review_epoch"]) + 1
            connection.execute(
                """
                UPDATE actions
                SET status = 'obsolete', last_error = 'pull request reopened', updated_at = ?
                WHERE repository = ? AND pr_number = ? AND base_sha = ? AND head_sha = ?
                  AND review_epoch = ? AND status IN ('pending', 'failed', 'dispatching')
                """,
                (utc_now(), *identity.values(), exact["review_epoch"]),
            )
            connection.execute(
                """
                UPDATE heads
                SET state = 'ci_running', source_updated_at = ?, head_started_at = ?, review_epoch = ?,
                    ci_conclusion = NULL, rail = NULL, review_request_id = NULL,
                    reviewer_actor = NULL, repair_owner = NULL, blocker = NULL,
                    updated_at = ?
                WHERE repository = ? AND pr_number = ? AND base_sha = ? AND head_sha = ?
                  AND is_current = 1
                """,
                (event["source_updated_at"], event["source_updated_at"], next_epoch, utc_now(), *identity.values()),
            )
            insert_event(
                connection,
                event_id=event_id,
                kind="pull_request.reopened",
                stale=False,
                payload=payload,
                **identity,
            )
            return {
                "result": "accepted",
                "state": "ci_running",
                "repair_cycle": exact["repair_cycle"],
                "review_epoch": next_epoch,
                "merge_dispatched": False,
            }
    previous = current_head(connection, event["repository"], event["pr_number"])
    if previous is not None and (
        previous["base_sha"] != event["base_sha"]
        or previous["head_sha"] != event["head_sha"]
    ):
        current_freshness = previous["source_updated_at"]
        if current_freshness and event["source_updated_at"] <= current_freshness:
            insert_event(
                connection,
                event_id=event_id,
                kind=f"pull_request.{event['action']}",
                stale=True,
                payload=payload,
                **identity,
            )
            return {
                "result": "stale",
                "state": previous["state"],
                "reason": "pull_request delivery is older than the current exact head",
                "repair_cycle": previous["repair_cycle"],
                "merge_dispatched": False,
            }
    row, created = begin_new_head(
        connection,
        **{
            key: event[key]
            for key in (
                "repository",
                "pr_number",
                "base_sha",
                "head_sha",
                "author",
                "source_updated_at",
            )
        },
    )
    insert_event(connection, event_id=event_id, kind=f"pull_request.{event['action']}", stale=False, payload=payload, **{k: event[k] for k in ("repository", "pr_number", "base_sha", "head_sha")})
    return {
        "result": "accepted" if created else "duplicate",
        "state": row["state"],
        "repair_cycle": row["repair_cycle"],
        "review_epoch": row["review_epoch"],
        "merge_dispatched": False,
    }


def process_workflow_run(
    connection: sqlite3.Connection,
    config: dict[str, Any],
    event_id: str,
    event: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    if event.get("rail") != "clawsweeper":
        _remember_ci_run_identity(connection, event, "seen")
    if "ignored" in event:
        outcome = {"result": "ignored", "reason": event["ignored"]}
        for key in (
            "rail",
            "workflow_run_id",
            "pr_number",
            "base_sha",
            "head_sha",
            "conclusion",
            "source_created_at",
        ):
            if key in event:
                outcome[key] = event[key]
        _set_ci_run_disposition(connection, event, "ignored_scope")
        return outcome
    if event.get("rail") == "clawsweeper":
        prior_run = connection.execute(
            "SELECT * FROM rail_workflow_runs WHERE rail = ? AND workflow_run_id = ?",
            (event["rail"], event["workflow_run_id"]),
        ).fetchone()
        if prior_run is not None:
            same_identity = (
                prior_run["workflow_name"] == event["workflow_name"]
                and prior_run["workflow_path"] == event["workflow_path"]
                and prior_run["workflow_head_sha"] == event["workflow_head_sha"]
                and prior_run["conclusion"] == event["conclusion"]
                and prior_run["source_created_at"] == event["source_created_at"]
            )
            if not same_identity:
                raise ContractError(
                    "ClawSweeper workflow_run identity was reused with conflicting terminal facts"
                )
        connection.execute(
            """
            INSERT INTO rail_workflow_runs(
              rail, workflow_run_id, event_id, workflow_name, workflow_path,
              workflow_head_sha, conclusion, status, source_created_at, received_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'terminal_pending_verdict_bridge', ?, ?)
            ON CONFLICT(rail, workflow_run_id) DO NOTHING
            """,
            (
                event["rail"],
                event["workflow_run_id"],
                event_id,
                event["workflow_name"],
                event["workflow_path"],
                event["workflow_head_sha"],
                event["conclusion"],
                event["source_created_at"],
                utc_now(),
            ),
        )
        return {
            "result": "duplicate" if prior_run is not None else "accepted",
            "state": "terminal_pending_verdict_bridge",
            "rail": "clawsweeper",
            "workflow_run_id": event["workflow_run_id"],
            "verdict_inferred": False,
            "merge_dispatched": False,
        }
    identity = {k: event[k] for k in ("repository", "pr_number", "base_sha", "head_sha")}
    row = exact_current_head(connection, **identity)
    if row is None:
        insert_event(connection, event_id=event_id, kind="ci.completed", stale=True, payload=payload, **identity)
        _set_ci_run_disposition(connection, event, "stale")
        return {"result": "stale", "reason": "CI tuple is not the current PR head", "merge_dispatched": False}
    if row["state"] in {"closed", "closed_merged"}:
        insert_event(
            connection,
            event_id=event_id,
            kind="ci.completed",
            stale=True,
            payload=payload,
            **identity,
        )
        _set_ci_run_disposition(connection, event, "ignored_terminal")
        return {
            "result": "ignored",
            "state": row["state"],
            "reason": "CI completed after the pull request reached a terminal state",
            "review_invoked": False,
            "merge_dispatched": False,
        }
    if event["source_created_at"] <= row["head_started_at"]:
        insert_event(
            connection,
            event_id=event_id,
            kind="ci.completed",
            stale=True,
            payload=payload,
            **identity,
        )
        _set_ci_run_disposition(connection, event, "ignored_old")
        return {
            "result": "ignored",
            "state": row["state"],
            "reason": "CI run was created before the current pull request incarnation",
            "review_invoked": False,
            "merge_dispatched": False,
        }
    policy = config.get("review_policy")
    if policy:
        require_github_timestamp(payload["workflow_run"].get("updated_at"), "CI completion timestamp")
    conclusion = event["conclusion"]
    action_id = None
    created = False
    if conclusion == "success":
        review_epoch = int(row["review_epoch"])
        action_payload = openclaw_action_payload(
            config,
            event["pr_number"],
            event["base_sha"],
            event["head_sha"],
            review_epoch,
        )
        action_id, created = insert_action(
            connection,
            kind="openclaw.enqueue",
            payload=action_payload,
            review_epoch=review_epoch,
            **identity,
        )
        if row["state"] in {"ci_running", "ci_failed", "openclaw_queued"}:
            connection.execute(
                "UPDATE heads SET state = 'openclaw_queued', ci_conclusion = ?, blocker = NULL, updated_at = ? WHERE repository = ? AND pr_number = ? AND base_sha = ? AND head_sha = ?",
                (conclusion, utc_now(), *identity.values()),
            )
    else:
        if row["state"] == "ci_running":
            connection.execute(
                "UPDATE heads SET state = 'ci_failed', ci_conclusion = ?, blocker = ?, updated_at = ? WHERE repository = ? AND pr_number = ? AND base_sha = ? AND head_sha = ?",
                (conclusion, f"CI concluded {conclusion}; Review Rails were not invoked", utc_now(), *identity.values()),
            )
    insert_event(connection, event_id=event_id, kind="ci.completed", stale=False, payload=payload, **identity)
    _set_ci_run_disposition(connection, event, "accepted")
    return {
        "result": "accepted",
        "state": "openclaw_queued" if conclusion == "success" else "ci_failed",
        "action_id": action_id,
        "action_created": created,
        "review_invoked": conclusion == "success",
        "merge_dispatched": False,
    }


def _recorded_ci_run(
    connection: sqlite3.Connection,
    event: dict[str, Any],
) -> bool:
    """Return whether this exact CI run was already admitted.

    Reconciliation may be invoked after GitHub delivered the original event but
    before the operator could observe the local receipt.  The run id is the
    authoritative operation identity; conflicting facts for that id fail
    closed instead of creating a second event.
    """
    row = connection.execute(
        """
        SELECT pr_number, base_sha, head_sha, conclusion, source_created_at, disposition
        FROM ci_run_identities
        WHERE repository = ? AND workflow_run_id = ?
        """,
        (event["repository"], str(event["run_id"])),
    ).fetchone()
    if row is None:
        return False
    if (
        row["pr_number"] != event["pr_number"]
        or row["base_sha"] != event["base_sha"]
        or row["head_sha"] != event["head_sha"]
        or row["conclusion"] != event["conclusion"]
        or row["source_created_at"] != event["source_created_at"]
    ):
        raise ContractError("CI workflow_run identity was reused with conflicting terminal facts")
    if row["disposition"] == "ignored_scope":
        raise ContractError("workflow_run identity was previously recorded as ignored")
    return True


def reconcile_workflow_run(
    *,
    config_path: Path,
    state_root: Path,
    payload: dict[str, Any],
    expected_pr_number: int,
    expected_base_sha: str,
    expected_head_sha: str,
    expected_run_id: int,
    admission_hook: Callable[[sqlite3.Connection, dict[str, Any], str, dict[str, Any], dict[str, Any]], dict[str, Any] | None] | None = None,
) -> dict[str, Any]:
    """Admit one authoritative read-back of a completed CI workflow run.

    This is an operator recovery boundary for a missing or unobservable
    webhook delivery.  It never polls, reruns CI, dispatches either review
    rail, or trusts a caller-provided tuple: the GitHub payload is parsed and
    the supplied identity is compared to the parsed exact values.
    """
    config = load_config(config_path)
    require_enabled(config)
    if not isinstance(payload, dict):
        raise ContractError("workflow_run read-back must be an object")
    try:
        canonical_payload = canonical_json(payload).encode("utf-8")
    except (RecursionError, TypeError, ValueError) as exc:
        raise ContractError("workflow_run read-back is not safely canonicalizable") from exc
    if len(canonical_payload) > WORKFLOW_READBACK_MAX_BYTES:
        raise ContractError("workflow_run read-back is oversized")
    if config.get("review_policy") and admission_hook is None:
        raise ContractError("strict workflow_run reconciliation requires service admission")
    event = parse_workflow_run_event(config, payload)
    if "ignored" in event:
        raise ContractError(f"workflow_run read-back is ignored: {event['ignored']}")
    if event.get("rail") == "clawsweeper":
        raise ContractError("workflow_run reconciliation accepts CI only")
    if (
        event["pr_number"] != require_positive_int(expected_pr_number, "expected PR number")
        or event["base_sha"] != require_sha(expected_base_sha, "expected base sha")
        or event["head_sha"] != require_sha(expected_head_sha, "expected head sha")
        or event["run_id"] != require_positive_int(expected_run_id, "expected workflow_run id")
    ):
        raise ContractError("workflow_run read-back does not match the requested exact tuple")
    delivery_id = f"github-reconcile-workflow-run:{event['run_id']}"
    payload_sha = hashlib.sha256(canonical_payload).hexdigest()
    connection = open_database(state_root, config["repository"])
    try:
        connection.execute("BEGIN IMMEDIATE")
        prior = connection.execute(
            "SELECT * FROM deliveries WHERE delivery_id = ?", (delivery_id,)
        ).fetchone()
        if prior is not None:
            if prior["source"] != "github-readback" or prior["event_type"] != "workflow_run":
                raise ContractError("workflow_run reconciliation id collided with another delivery")
            if prior["payload_sha256"] != payload_sha:
                raise ContractError("workflow_run reconciliation id was reused with different content")
            connection.rollback()
            outcome = json.loads(prior["outcome_json"])
            outcome["result"] = "duplicate_reconciliation"
            return outcome
        if _recorded_ci_run(connection, event):
            outcome = {
                "result": "already_processed",
                "repository": event["repository"],
                "pr_number": event["pr_number"],
                "base_sha": event["base_sha"],
                "head_sha": event["head_sha"],
                "run_id": event["run_id"],
                "review_invoked": False,
                "merge_dispatched": False,
            }
        else:
            outcome = process_workflow_run(
                connection,
                config,
                event_id_from_delivery(delivery_id),
                event,
                payload,
            )
        binding = None
        if admission_hook is not None:
            binding = admission_hook(connection, config, "workflow_run", payload, outcome)
        if config.get("review_policy"):
            if binding is None:
                raise ContractError(
                    "strict workflow_run reconciliation requires an exact current binding"
                )
            binding = require_object(binding, "workflow_run reconciliation admission binding")
            for key in ("repository", "pr_number", "base_sha", "head_sha"):
                if binding.get(key) != event[key]:
                    raise ContractError(
                        "workflow_run reconciliation admission binding does not match the exact tuple"
                    )
        if binding is not None:
            binding = require_object(binding, "workflow_run reconciliation admission binding")
            outcome["admission_binding_id"] = require_text(
                binding.get("binding_id"), "admission binding id", 64
            )
        outcome.update(
            {
                "schema": "smoky.review-conductor.reconciliation-receipt.v1",
                "delivery_id": delivery_id,
                "event_type": "workflow_run",
                "source": "github-readback",
                "payload_sha256": payload_sha,
            }
        )
        connection.execute(
            "INSERT INTO deliveries(delivery_id, source, event_type, payload_sha256, received_at, outcome_json) VALUES (?, 'github-readback', 'workflow_run', ?, ?, ?)",
            (delivery_id, payload_sha, utc_now(), canonical_json(outcome)),
        )
        connection.commit()
        return outcome
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def reconcile_workflow_run_command(args: argparse.Namespace) -> dict[str, Any]:
    try:
        with args.body_file.open("rb") as body_file:
            body = body_file.read(WORKFLOW_READBACK_MAX_BYTES + 1)
    except OSError as exc:
        raise ContractError("cannot read workflow_run read-back JSON") from exc
    if len(body) > WORKFLOW_READBACK_MAX_BYTES:
        raise ContractError("workflow_run read-back is oversized")
    verify_github_signature(body, args.signature, os.environ.get(args.secret_env, ""))
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, RecursionError, ValueError) as exc:
        raise ContractError("cannot read workflow_run read-back JSON") from exc
    return reconcile_workflow_run(
        config_path=args.config,
        state_root=args.state_root,
        payload=payload,
        expected_pr_number=args.pr_number,
        expected_base_sha=args.base_sha,
        expected_head_sha=args.head_sha,
        expected_run_id=args.run_id,
    )


def verify_github_signature(body: bytes, signature: str, secret: str) -> None:
    if not secret:
        raise ContractError("webhook secret is empty")
    if not re.fullmatch(r"sha256=[0-9a-f]{64}", signature or ""):
        raise ContractError("X-Hub-Signature-256 is missing or malformed")
    expected = "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise ContractError("GitHub webhook signature did not match")


def ingest_github_delivery(
    *,
    config_path: Path,
    state_root: Path,
    event_type: str,
    delivery_id: str,
    signature: str,
    body: bytes,
    secret: str,
    admission_hook: Callable[[sqlite3.Connection, dict[str, Any], str, dict[str, Any], dict[str, Any]], dict[str, Any] | None] | None = None,
) -> dict[str, Any]:
    if not SAFE_ID_RE.fullmatch(delivery_id):
        raise ContractError("delivery id is unsafe")
    if event_type not in GITHUB_EVENT_TYPES:
        raise ContractError("GitHub event type must be pull_request, workflow_run, or issue_comment")
    verify_github_signature(body, signature, secret)
    config = load_config(config_path)
    try:
        payload = require_object(json.loads(body.decode("utf-8")), "GitHub webhook payload")
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError("GitHub webhook payload is not valid UTF-8 JSON") from exc
    payload_sha = hashlib.sha256(body).hexdigest()
    require_enabled(config)
    connection = open_database(state_root, config["repository"])
    try:
        connection.execute("BEGIN IMMEDIATE")
        prior = connection.execute(
            "SELECT * FROM deliveries WHERE delivery_id = ?", (delivery_id,)
        ).fetchone()
        if prior is not None:
            if prior["payload_sha256"] != payload_sha or prior["event_type"] != event_type:
                raise ContractError("delivery id was already used for different content")
            connection.rollback()
            outcome = json.loads(prior["outcome_json"])
            outcome["result"] = "duplicate_delivery"
            return outcome
        event_id = event_id_from_delivery(delivery_id)
        if event_type == "pull_request":
            event = parse_pull_request_event(config, payload)
            outcome = process_pull_request(connection, config, event_id, event, payload)
        elif event_type == "workflow_run":
            event = parse_workflow_run_event(config, payload)
            if "ignored" in event or event.get("rail") == "clawsweeper":
                outcome = process_workflow_run(connection, config, event_id, event, payload)
            elif _recorded_ci_run(connection, event):
                outcome = {
                    "result": "duplicate",
                    "repository": event["repository"],
                    "pr_number": event["pr_number"],
                    "base_sha": event["base_sha"],
                    "head_sha": event["head_sha"],
                    "run_id": event["run_id"],
                    "merge_dispatched": False,
                }
            else:
                outcome = process_workflow_run(connection, config, event_id, event, payload)
        else:
            event = parse_issue_comment_event(config, payload)
            outcome = process_issue_comment(connection, config, event_id, event, payload)
        if admission_hook is not None:
            binding = admission_hook(connection, config, event_type, payload, outcome)
            if binding is not None:
                outcome["admission_binding_id"] = require_text(
                    binding.get("binding_id"), "admission binding id", 64
                )
        outcome.update({"schema": "smoky.review-conductor.receipt.v1", "delivery_id": delivery_id, "event_type": event_type})
        connection.execute(
            "INSERT INTO deliveries(delivery_id, source, event_type, payload_sha256, received_at, outcome_json) VALUES (?, 'github', ?, ?, ?, ?)",
            (delivery_id, event_type, payload_sha, utc_now(), canonical_json(outcome)),
        )
        connection.commit()
        return outcome
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def github_delivery(args: argparse.Namespace) -> dict[str, Any]:
    try:
        body = args.body_file.read_bytes()
    except OSError as exc:
        raise ContractError(f"cannot read GitHub webhook body: {exc.__class__.__name__}") from exc
    return ingest_github_delivery(
        config_path=args.config,
        state_root=args.state_root,
        event_type=args.event_type,
        delivery_id=args.delivery_id,
        signature=args.signature,
        body=body,
        secret=os.environ.get(args.secret_env, ""),
    )


def validate_internal_event(config: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
    common = {"schema", "event_id", "type", "repository", "pr_number", "base_sha", "head_sha"}
    missing_common = common - event.keys()
    if missing_common:
        raise ContractError(
            f"internal event is missing fields: {', '.join(sorted(missing_common))}"
        )
    if event["schema"] != INTERNAL_EVENT_SCHEMA:
        raise ContractError("internal event schema is unsupported")
    event_id = require_text(event["event_id"], "internal event id", 200)
    if not SAFE_ID_RE.fullmatch(event_id):
        raise ContractError("internal event id is unsafe")
    event_type = require_text(event["type"], "internal event type", 100)
    if event_type not in INTERNAL_EVENT_FIELDS:
        raise ContractError("internal event type is unsupported")
    required, optional = INTERNAL_EVENT_FIELDS[event_type]
    if config.get("review_policy") and event_type.endswith(".terminal"):
        required = required | {"review_scope"}
        if event_type == "openclaw.terminal":
            required = required | {
                "native_max_priority",
                "applied_max_priority",
                "exact_tuple_qualified",
            }
    if config.get("review_policy"):
        common = common | {"review_epoch"}
        epoch = event.get("review_epoch")
        if type(epoch) is not int or epoch < 0:
            raise ContractError("internal event requires an exact review epoch")
    require_exact_keys(event, common | required, optional, "internal event")
    if config.get("review_policy") and event_type.endswith(".terminal"):
        rail = event_type.split(".")[0]
        if event.get("review_scope") != "comprehensive" or event.get("reviewer_actor") != config["review_policy"]["reviewers"][rail]:
            raise ContractError("terminal review must be comprehensive and from the authoritative repository reviewer")
        if event_type == "openclaw.terminal" and not has_openclaw_applied_p3_qualification(event):
            raise ContractError(
                "OpenClaw terminal lacks trusted comprehensive exact-tuple qualification"
            )
    if event["repository"] != config["repository"]:
        raise ContractError("internal event repository does not match config")
    require_positive_int(event["pr_number"], "internal event pr_number")
    require_sha(event["base_sha"], "internal event base_sha")
    require_sha(event["head_sha"], "internal event head_sha")
    if event_type == "openclaw.terminal":
        if "original_report" in event:
            validate_original_report(event["original_report"])
        for field in ("proof_sha256", "artifact_digest"):
            if field in event and (not isinstance(event[field], str) or
                                   re.fullmatch(r"[0-9a-f]{64}", event[field]) is None):
                raise ContractError(f"{field} must be lowercase SHA-256")
        if "review_epoch" in event and (type(event["review_epoch"]) is not int or event["review_epoch"] < 0):
            raise ContractError("OpenClaw terminal requires an exact review epoch")
    if event_type.startswith("openclaw."):
        require_text(event.get("request_id"), "OpenClaw request_id", 200)
    if event_type.startswith("clawsweeper."):
        run_id = event.get("workflow_run_id")
        if not isinstance(run_id, (str, int)) or isinstance(run_id, bool) or not str(run_id):
            raise ContractError("ClawSweeper workflow_run_id is required")
    if event_type.endswith(".terminal"):
        if "proof_sha256" in event and (not isinstance(event["proof_sha256"], str) or
                                       re.fullmatch(r"[0-9a-f]{64}", event["proof_sha256"]) is None):
            raise ContractError("proof_sha256 must be lowercase SHA-256")
        if event.get("result") not in TERMINAL_RESULTS:
            raise ContractError("terminal result must be clean, findings, failed, or human_gate")
        require_text(event.get("proof_ref"), "terminal proof_ref", 500)
        require_text(event.get("reviewer_actor"), "terminal reviewer_actor", 200)
        finding_count = event.get("finding_count")
        if isinstance(finding_count, bool) or not isinstance(finding_count, int) or finding_count < 0:
            raise ContractError("terminal finding_count must be a non-negative integer")
        if event["result"] == "clean" and finding_count != 0:
            raise ContractError("clean terminal result must have zero findings")
        if event["result"] == "findings" and finding_count == 0:
            raise ContractError("findings terminal result must have at least one finding")
    if event_type == "adjudication.completed":
        if config.get("review_policy") and event.get("reviewer_actor") != config["review_policy"]["reviewers"].get(event.get("rail")):
            raise ContractError("adjudication reviewer must match the authoritative repository reviewer")
        if event.get("rail") not in {"openclaw", "clawsweeper"}:
            raise ContractError("adjudication rail must be openclaw or clawsweeper")
        require_text(event.get("request_id"), "adjudication request_id", 200)
        require_text(event.get("reviewer_actor"), "adjudication reviewer_actor", 200)
        require_text(event.get("proof_ref"), "adjudication proof_ref", 500)
        classifications = event.get("classifications")
        if not isinstance(classifications, list) or not classifications:
            raise ContractError("adjudication classifications must be a non-empty array")
        if any(item not in CLASSIFICATIONS for item in classifications):
            raise ContractError("adjudication contains an unsupported classification")
        if len(classifications) != len(set(classifications)):
            raise ContractError("adjudication classifications must be unique")
        repair_owner = event.get("repair_owner")
        if repair_owner is not None:
            require_text(repair_owner, "adjudication repair_owner", 200)
        handoff = event.get("mutation_handoff")
        if handoff is not None:
            handoff = require_object(handoff, "adjudication mutation_handoff")
            require_exact_keys(handoff, {"from", "to", "authorized"}, set(), "adjudication mutation_handoff")
            require_text(handoff["from"], "mutation_handoff from", 200)
            require_text(handoff["to"], "mutation_handoff to", 200)
            if handoff["authorized"] is not True:
                raise ContractError("mutation handoff must be explicitly authorized")
        if "review_epoch" in event and (type(event["review_epoch"]) is not int or event["review_epoch"] < 0):
            raise ContractError("internal event requires an exact review epoch")
    return event


def update_exact_head(connection: sqlite3.Connection, identity: dict[str, Any], **fields: Any) -> None:
    allowed = {"state", "ci_conclusion", "rail", "review_request_id", "reviewer_actor", "repair_owner", "blocker", "mutation_owner"}
    if not fields or fields.keys() - allowed:
        raise ContractError("internal state update requested unsupported fields")
    assignments = ", ".join(f"{key} = ?" for key in fields) + ", updated_at = ?"
    values = [*fields.values(), utc_now(), *identity.values()]
    connection.execute(
        f"UPDATE heads SET {assignments} WHERE repository = ? AND pr_number = ? AND base_sha = ? AND head_sha = ? AND is_current = 1",
        values,
    )


def tuple_action(
    connection: sqlite3.Connection,
    identity: dict[str, Any],
    kind: str,
    review_epoch: int,
) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT * FROM actions
        WHERE repository = ? AND pr_number = ? AND base_sha = ? AND head_sha = ?
          AND kind = ? AND review_epoch = ?
        ORDER BY created_at, action_id
        LIMIT 1
        """,
        (*identity.values(), kind, review_epoch),
    ).fetchone()


def require_bound_clawsweeper_quality_workflow_run(
    workflow_run_id: str,
    quality_workflow_run_id: Any,
) -> None:
    if str(quality_workflow_run_id) != workflow_run_id:
        raise ContractError(
            "clawsweeper quality workflow_run_id does not match the exact request"
        )


def accepted_clawsweeper_quality(
    connection: sqlite3.Connection,
    identity: dict[str, Any],
    *,
    review_epoch: int,
    workflow_run_id: str,
) -> dict[str, Any] | None:
    tables = {
        name
        for (name,) in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    if "clawsweeper_quality" not in tables:
        return None
    quality = connection.execute(
        """
        SELECT * FROM clawsweeper_quality
        WHERE repository = ? AND pr_number = ? AND base_sha = ? AND head_sha = ?
          AND review_epoch = ?
        """,
        (
            identity["repository"],
            identity["pr_number"],
            identity["base_sha"],
            identity["head_sha"],
            review_epoch,
        ),
    ).fetchone()
    if quality is None:
        return None
    require_bound_clawsweeper_quality_workflow_run(
        workflow_run_id, quality["workflow_run_id"]
    )
    return dict(quality)


def accepted_clawsweeper_terminal(
    connection: sqlite3.Connection,
    identity: dict[str, Any],
    *,
    review_epoch: int,
    workflow_run_id: str,
) -> dict[str, Any] | None:
    found = connection.execute(
        """
        SELECT payload_json FROM events
        WHERE kind = 'clawsweeper.terminal'
          AND repository = ? AND pr_number = ? AND base_sha = ? AND head_sha = ?
          AND stale = 0
        ORDER BY sequence DESC
        """,
        (
            identity["repository"],
            identity["pr_number"],
            identity["base_sha"],
            identity["head_sha"],
        ),
    )
    for candidate in found:
        payload = json.loads(candidate["payload_json"])
        if not isinstance(payload, dict):
            continue
        epoch = payload.get("review_epoch")
        if epoch is not None and (type(epoch) is not int or epoch != review_epoch):
            continue
        if str(payload.get("workflow_run_id", "")) != workflow_run_id:
            continue
        return payload
    return None


def continue_after_adjudication(
    connection: sqlite3.Connection,
    config: dict[str, Any],
    row: sqlite3.Row,
    identity: dict[str, Any],
    rail: str,
) -> tuple[str, str | None, bool]:
    if rail == "openclaw":
        if bool(row["is_draft"]):
            update_exact_head(
                connection,
                identity,
                state="openclaw_clean_draft",
                rail="openclaw",
                blocker="ClawSweeper waits for ready-for-review state",
            )
            return "openclaw_clean_draft", None, False
        review_epoch = int(row["review_epoch"])
        payload = clawsweeper_action_payload(
            config,
            identity["pr_number"],
            identity["base_sha"],
            identity["head_sha"],
            review_epoch,
        )
        action_id, created = insert_action(
            connection,
            kind="clawsweeper.dispatch",
            payload=payload,
            review_epoch=review_epoch,
            **identity,
        )
        update_exact_head(connection, identity, state="clawsweeper_queued", rail="clawsweeper", review_request_id=None, blocker=None)
        return "clawsweeper_queued", action_id, created
    if bool(row["is_draft"]):
        update_exact_head(
            connection,
            identity,
            state="clawsweeper_clean_draft",
            rail="clawsweeper",
            blocker="ready-for-human clearance is paused while the pull request is draft",
        )
        return "clawsweeper_clean_draft", None, False
    update_exact_head(connection, identity, state="ready_for_human_merge", blocker="human merge authority required")
    return "ready_for_human_merge", None, False


def process_internal_event(connection: sqlite3.Connection, config: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
    require_enabled(config)
    identity = {key: event[key] for key in ("repository", "pr_number", "base_sha", "head_sha")}
    row = exact_current_head(connection, **identity)
    if row is None:
        insert_event(connection, event_id=event["event_id"], kind=event["type"], stale=True, payload=event, **identity)
        return {"schema": "smoky.review-conductor.receipt.v1", "event_id": event["event_id"], "result": "stale", "reason": "event tuple is not the current PR head", "merge_dispatched": False}
    if config.get("review_policy") and event["review_epoch"] != row["review_epoch"]:
        raise ContractError("internal event review epoch is stale")
    event_type = event["type"]
    action_id: str | None = None
    action_created = False
    if event_type == "openclaw.started":
        if row["state"] != "openclaw_queued":
            raise ContractError(f"OpenClaw start is invalid from state {row['state']}")
        enqueue = tuple_action(
            connection, identity, "openclaw.enqueue", int(row["review_epoch"])
        )
        if (
            enqueue is None
            or enqueue["status"] != "dispatched"
            or json.loads(enqueue["payload_json"])["queue_request_id"] != event["request_id"]
        ):
            raise ContractError("OpenClaw start does not match the exact queued action request_id")
        update_exact_head(connection, identity, state="openclaw_running", rail="openclaw", review_request_id=event["request_id"], blocker=None)
        next_state = "openclaw_running"
    elif event_type == "openclaw.terminal":
        if row["state"] not in {"openclaw_queued", "openclaw_running"}:
            raise ContractError(f"OpenClaw terminal event is invalid from state {row['state']}")
        enqueue = tuple_action(
            connection, identity, "openclaw.enqueue", int(row["review_epoch"])
        )
        if (
            enqueue is None
            or enqueue["status"] != "dispatched"
            or json.loads(enqueue["payload_json"])["queue_request_id"] != event["request_id"]
        ):
            raise ContractError("OpenClaw terminal does not match the exact queued action request_id")
        if row["review_request_id"] and row["review_request_id"] != event["request_id"]:
            raise ContractError("OpenClaw terminal request_id does not match the running request")
        if event["result"] == "clean" and bool(row["is_draft"]):
            next_state = "openclaw_clean_draft"
            update_exact_head(connection, identity, state=next_state, rail="openclaw", review_request_id=event["request_id"], reviewer_actor=event["reviewer_actor"], blocker="ClawSweeper waits for ready-for-review state")
        elif event["result"] == "clean":
            review_epoch = int(row["review_epoch"])
            payload = clawsweeper_action_payload(
                config,
                identity["pr_number"],
                identity["base_sha"],
                identity["head_sha"],
                review_epoch,
            )
            action_id, action_created = insert_action(
                connection,
                kind="clawsweeper.dispatch",
                payload=payload,
                review_epoch=review_epoch,
                **identity,
            )
            next_state = "clawsweeper_queued"
            update_exact_head(connection, identity, state=next_state, rail="clawsweeper", review_request_id=None, reviewer_actor=event["reviewer_actor"], blocker=None)
        elif event["result"] == "findings":
            next_state = "awaiting_adjudication"
            update_exact_head(connection, identity, state=next_state, rail="openclaw", review_request_id=event["request_id"], reviewer_actor=event["reviewer_actor"], blocker="OpenClaw findings require bounded adjudication")
        elif event["result"] == "human_gate":
            next_state = "waiting_human"
            update_exact_head(connection, identity, state=next_state, rail="openclaw", review_request_id=event["request_id"], reviewer_actor=event["reviewer_actor"], blocker="OpenClaw review requires a human decision")
        else:
            next_state = "openclaw_failed"
            update_exact_head(connection, identity, state=next_state, rail="openclaw", review_request_id=event["request_id"], reviewer_actor=event["reviewer_actor"], blocker="OpenClaw rail failed; human/operator recovery required")
    elif event_type == "clawsweeper.started":
        if row["state"] != "clawsweeper_queued":
            raise ContractError(f"ClawSweeper start is invalid from state {row['state']}")
        dispatch = current_clawsweeper_dispatch(
            connection, identity, int(row["review_epoch"])
        )
        run_id = str(event["workflow_run_id"])
        if dispatch is None:
            raise ContractError("ClawSweeper start does not match a dispatched exact-tuple action")
        mismatch = clawsweeper_attempt_mismatch(dispatch, run_id)
        if mismatch is not None:
            insert_event(connection, event_id=event["event_id"], kind=event_type, stale=True, payload=event, **identity)
            return {"schema": "smoky.review-conductor.receipt.v1", "event_id": event["event_id"], "result": "stale", "reason": mismatch, "state": row["state"], "merge_dispatched": False}
        if dispatch["status"] != "dispatched":
            raise ContractError("ClawSweeper start does not match a dispatched exact-tuple action")
        receipt = connection.execute(
            "SELECT status FROM rail_workflow_runs WHERE rail = 'clawsweeper' AND workflow_run_id = ?",
            (run_id,),
        ).fetchone()
        if receipt is not None and receipt["status"] in {
            "terminal_attention_required", "verdict_ingested",
        }:
            # A delayed start cannot revive a retired receipt. Its alert/evidence
            # remains authoritative; only explicit recovery may reopen the work.
            raise ContractError("ClawSweeper start refers to a retired terminal workflow; operator recovery required")
        update_exact_head(connection, identity, state="clawsweeper_running", rail="clawsweeper", review_request_id=run_id, blocker=None)
        next_state = "clawsweeper_running"
    elif event_type == "clawsweeper.terminal":
        if row["state"] not in {"clawsweeper_queued", "clawsweeper_running"}:
            raise ContractError(f"ClawSweeper terminal event is invalid from state {row['state']}")
        dispatch = current_clawsweeper_dispatch(
            connection, identity, int(row["review_epoch"])
        )
        run_id = str(event["workflow_run_id"])
        if dispatch is None:
            raise ContractError("ClawSweeper terminal does not match a dispatched exact-tuple action")
        mismatch = clawsweeper_attempt_mismatch(dispatch, run_id)
        if mismatch is not None:
            insert_event(connection, event_id=event["event_id"], kind=event_type, stale=True, payload=event, **identity)
            return {"schema": "smoky.review-conductor.receipt.v1", "event_id": event["event_id"], "result": "stale", "reason": mismatch, "state": row["state"], "merge_dispatched": False}
        if dispatch["status"] != "dispatched":
            raise ContractError("ClawSweeper terminal does not match a dispatched exact-tuple action")
        if row["review_request_id"] and row["review_request_id"] != run_id:
            raise ContractError("ClawSweeper terminal workflow_run_id does not match the running request")
        if event["result"] == "clean" and bool(row["is_draft"]):
            next_state = "clawsweeper_clean_draft"
            update_exact_head(
                connection,
                identity,
                state=next_state,
                rail="clawsweeper",
                review_request_id=None,
                reviewer_actor=event["reviewer_actor"],
                blocker="ready-for-human clearance is paused while the pull request is draft",
            )
        elif event["result"] == "clean":
            next_state = "ready_for_human_merge"
            update_exact_head(connection, identity, state=next_state, rail="clawsweeper", review_request_id=str(event["workflow_run_id"]), reviewer_actor=event["reviewer_actor"], blocker="human merge authority required")
        elif event["result"] == "findings":
            next_state = "awaiting_adjudication"
            update_exact_head(connection, identity, state=next_state, rail="clawsweeper", review_request_id=str(event["workflow_run_id"]), reviewer_actor=event["reviewer_actor"], blocker="ClawSweeper findings require bounded adjudication")
        elif event["result"] == "human_gate":
            next_state = "waiting_human"
            update_exact_head(connection, identity, state=next_state, rail="clawsweeper", review_request_id=str(event["workflow_run_id"]), reviewer_actor=event["reviewer_actor"], blocker="ClawSweeper verdict requires a human decision")
        else:
            next_state = "clawsweeper_failed"
            update_exact_head(connection, identity, state=next_state, rail="clawsweeper", review_request_id=str(event["workflow_run_id"]), reviewer_actor=event["reviewer_actor"], blocker="ClawSweeper rail failed; human/operator recovery required")
    else:
        classifications = set(event["classifications"])
        if row["state"] == "waiting_human":
            if row["rail"] != "clawsweeper":
                raise ContractError(f"adjudication is invalid from state {row['state']}")
            if not classifications <= {"defer", "reject_false_positive"}:
                raise ContractError(
                    "waiting_human clawsweeper adjudication supports only defer and reject_false_positive"
                )
            if type(event.get("review_epoch")) is not int or event["review_epoch"] != row["review_epoch"]:
                raise ContractError("internal event review epoch is stale")
            if event.get("reviewer_actor") != row["reviewer_actor"]:
                raise ContractError("adjudication reviewer must match the authoritative repository reviewer")
        elif row["state"] != "awaiting_adjudication":
            raise ContractError(f"adjudication is invalid from state {row['state']}")
        if row["rail"] != event["rail"] or row["review_request_id"] != event["request_id"]:
            raise ContractError("adjudication does not match the exact rail request")
        if row["state"] == "waiting_human":
            terminal = accepted_clawsweeper_terminal(
                connection,
                identity,
                review_epoch=int(row["review_epoch"]),
                workflow_run_id=str(row["review_request_id"]),
            )
            if terminal is None or terminal.get("result") != "human_gate":
                raise ContractError(
                    "waiting_human clawsweeper adjudication requires the exact human_gate terminal"
                )
            accepted_clawsweeper_quality(
                connection,
                identity,
                review_epoch=int(row["review_epoch"]),
                workflow_run_id=str(row["review_request_id"]),
            )
            next_state, action_id, action_created = continue_after_adjudication(
                connection, config, row, identity, event["rail"]
            )
        elif "human_gate" in classifications:
            next_state = "waiting_human"
            update_exact_head(connection, identity, state=next_state, blocker="adjudication classified a human gate")
        elif "required_fix" in classifications:
            selected_owner = event.get("repair_owner") or row["author"]
            if selected_owner == event["reviewer_actor"]:
                raise ContractError("a reviewer may not own repair of its own findings")
            if selected_owner != row["mutation_owner"]:
                handoff = event.get("mutation_handoff")
                if not handoff or handoff["from"] != row["mutation_owner"] or handoff["to"] != selected_owner:
                    raise ContractError("fallback repair owner requires an exact explicit mutation handoff")
            current_cycle = int(row["repair_cycle"])
            ledger_limit = int(config["max_repair_cycles"])
            next_cycle = current_cycle + 1 if current_cycle < ledger_limit else current_cycle
            route_payload = {
                "schema": "smoky.review-conductor.action.v1",
                "kind": "repair.route",
                **identity,
                "review_epoch": int(row["review_epoch"]),
                "repair_owner": selected_owner,
                "source_rail": event["rail"],
                "review_request_id": event["request_id"],
                "repair_cycle": next_cycle,
                "mutation_owner_count": 1,
                "merge_authorized": False,
            }
            action_id, action_created = insert_action(
                connection,
                kind="repair.route",
                payload=route_payload,
                suffix=f"{event['rail']}:{event['request_id']}",
                review_epoch=int(row["review_epoch"]),
                **identity,
            )
            next_state = "repair_required"
            update_exact_head(connection, identity, state=next_state, repair_owner=selected_owner, mutation_owner=selected_owner, blocker="accepted required fix must be patched by the single mutation owner")
        else:
            next_state, action_id, action_created = continue_after_adjudication(connection, config, row, identity, event["rail"])
    insert_event(connection, event_id=event["event_id"], kind=event_type, stale=False, payload=event, **identity)
    return {
        "schema": "smoky.review-conductor.receipt.v1",
        "event_id": event["event_id"],
        "result": "accepted",
        "state": next_state,
        "action_id": action_id,
        "action_created": action_created,
        "repair_cycle": row["repair_cycle"],
        "merge_dispatched": False,
    }


def ingest_internal_event(
    *, config_path: Path, state_root: Path, event_payload: dict[str, Any]
) -> dict[str, Any]:
    config = load_config(config_path)
    event = validate_internal_event(config, event_payload)
    require_enabled(config)
    connection = open_database(state_root, config["repository"])
    try:
        connection.execute("BEGIN IMMEDIATE")
        prior = connection.execute("SELECT payload_json FROM events WHERE event_id = ?", (event["event_id"],)).fetchone()
        if prior is not None:
            if prior["payload_json"] != canonical_json(event):
                raise ContractError("internal event id was already used for different content")
            connection.rollback()
            return {"schema": "smoky.review-conductor.receipt.v1", "event_id": event["event_id"], "result": "duplicate_event", "merge_dispatched": False}
        outcome = process_internal_event(connection, config, event)
        connection.commit()
        return outcome
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def internal_event(args: argparse.Namespace) -> dict[str, Any]:
    return ingest_internal_event(
        config_path=args.config,
        state_root=args.state_root,
        event_payload=read_json(args.event_file, "internal event"),
    )


def parse_json_receipt(stdout: str, label: str) -> dict[str, Any]:
    for line in reversed(stdout.splitlines()):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise ContractError(f"{label} did not emit a JSON receipt")


def run_command(
    command: list[str],
    label: str,
    *,
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        result = run_generation_bound(
            subprocess.run,
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=1800,
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ContractError(f"{label} could not complete: {exc.__class__.__name__}") from exc
    if result.returncode != 0:
        raise ContractError(f"{label} failed with exit {result.returncode}")
    return result


def command_preview(
    action: sqlite3.Row,
    config: dict[str, Any],
    source_checkout: Path | None,
    environment: dict[str, str] | None = None,
) -> list[list[str]]:
    payload = json.loads(action["payload_json"])
    command_environment = os.environ if environment is None else environment
    if action["kind"] == "openclaw.enqueue":
        if source_checkout is None or not source_checkout.is_absolute():
            raise ContractError("OpenClaw dispatch requires an absolute --source-checkout")
        review_epoch = bound_openclaw_queue_epoch(action, payload)
        smoky = command_environment.get("SMOKY_REVIEW_CONDUCTOR_SMOKY", str(Path(__file__).resolve().parents[1] / "bin" / "smoky"))
        materialize = [
            smoky, "lane", "run", "spark-openclaw-materialize-worktree",
            "--repo", str(source_checkout), "--ref", payload["head_sha"], "--base", payload["base_sha"],
            "--remote-worktree", payload["remote_worktree"], "--pr-url", payload["pr_url"],
            "--transport", payload["transport"],
        ]
        queue = [
            smoky, "lane", "run", "spark-openclaw-autoreview", "--queue",
            "--queue-request-id", payload["queue_request_id"], "--operator-id", payload["operator_id"],
            "--mode", "branch", "--base", payload["base_sha"], "--remote-worktree", payload["remote_worktree"],
            "--pr-url", payload["pr_url"],
        ]
        if declares_openclaw_exact_tuple_contract(config):
            queue.extend([
                "--exact-tuple-contract", OPENCLAW_EXACT_TUPLE_CONTRACT,
                "--review-epoch", str(review_epoch),
            ])
        return [materialize, queue]
    if action["kind"] == "clawsweeper.dispatch":
        gh = command_environment.get("SMOKY_REVIEW_CONDUCTOR_GH", "gh")
        endpoint = f"repos/{payload['repository']}/actions/workflows/{payload['workflow_id']}/dispatches"
        return [[
            gh, "api", "--method", "POST", endpoint,
            "-f", f"ref={payload['ref']}",
            "-F", f"inputs[pr_number]={payload['pr_number']}",
            "-F", f"inputs[expected_base_sha]={payload['base_sha']}",
            "-F", f"inputs[expected_head_sha]={payload['head_sha']}",
            "-F", "inputs[publish]=true",
        ]]
    raise ContractError(f"action kind {action['kind']} is an external owner handoff, not a conductor dispatch")


def dispatch_block_reason(
    head: sqlite3.Row | None,
    *,
    action_kind: str | None = None,
    review_epoch: int | None = None,
) -> str | None:
    if head is None:
        return "exact tuple is no longer current"
    if review_epoch is not None and int(head["review_epoch"]) != review_epoch:
        return "action review epoch is superseded"
    if head["state"] in {"closed", "closed_merged"}:
        return f"pull request is {head['state']}"
    required_state = {
        "openclaw.enqueue": "openclaw_queued",
        "clawsweeper.dispatch": "clawsweeper_queued",
    }.get(action_kind)
    if required_state is not None and head["state"] != required_state:
        return f"{action_kind} prerequisite state is {required_state}, not {head['state']}"
    return None


def dispatch_action(args: argparse.Namespace) -> dict[str, Any]:
    config = load_config(args.config)
    require_enabled(config)
    connection = open_database(args.state_root, config["repository"])
    try:
        row = connection.execute("SELECT * FROM actions WHERE action_id = ?", (args.action_id,)).fetchone()
        if row is None:
            raise ContractError("action id was not found")
        if row["repository"] != config["repository"]:
            raise ContractError("action repository does not match config")
        if config.get("review_policy") and row["kind"] == "clawsweeper.dispatch" and args.apply:
            raise ContractError("generalized ClawSweeper dispatch requires the repository-specific GitHub App adapter")
        exact = exact_current_head(
            connection,
            row["repository"],
            row["pr_number"],
            row["base_sha"],
            row["head_sha"],
        )
        blocked_reason = dispatch_block_reason(
            exact,
            action_kind=row["kind"],
            review_epoch=int(row["review_epoch"]),
        )
        if blocked_reason is not None and row["status"] != "dispatched":
            connection.execute(
                "UPDATE actions SET status = 'obsolete', last_error = ?, updated_at = ? WHERE action_id = ?",
                (blocked_reason, utc_now(), args.action_id),
            )
            connection.commit()
            return {
                "schema": "smoky.review-conductor.dispatch.v1",
                "result": "obsolete",
                "action_id": args.action_id,
                "external_mutation_performed": False,
                "merge_dispatched": False,
            }
        if row["status"] == "obsolete":
            return {
                "schema": "smoky.review-conductor.dispatch.v1",
                "result": "obsolete",
                "action_id": args.action_id,
                "external_mutation_performed": False,
                "merge_dispatched": False,
            }
        command_environment = getattr(args, "command_environment", None)
        commands = command_preview(
            row,
            config,
            args.source_checkout,
            command_environment,
        )
        if not args.apply:
            return {
                "schema": "smoky.review-conductor.dispatch.v1",
                "result": "planned",
                "action_id": args.action_id,
                "kind": row["kind"],
                "commands": commands,
                "external_mutation_performed": False,
                "merge_dispatched": False,
            }
        if row["status"] == "dispatched":
            return {"schema": "smoky.review-conductor.dispatch.v1", "result": "already_dispatched", "action_id": args.action_id, "receipt": json.loads(row["receipt_json"] or "{}"), "merge_dispatched": False}
        if row["status"] == "dispatching" and not (args.retry and row["kind"] == "openclaw.enqueue"):
            raise ContractError("action is in uncertain dispatching state; reconcile it before retrying")
        if row["status"] == "failed" and not (args.retry and row["kind"] == "openclaw.enqueue"):
            raise ContractError("failed action requires explicit idempotent OpenClaw --retry or human reconciliation")
        expected_status = row["status"]
        expected_attempts = int(row["attempts"])
        claimed_attempts = expected_attempts + 1
        connection.execute("BEGIN IMMEDIATE")
        locked_exact = exact_current_head(
            connection,
            row["repository"],
            row["pr_number"],
            row["base_sha"],
            row["head_sha"],
        )
        locked_blocked_reason = dispatch_block_reason(
            locked_exact,
            action_kind=row["kind"],
            review_epoch=int(row["review_epoch"]),
        )
        if locked_blocked_reason is not None:
            connection.execute(
                """
                UPDATE actions
                SET status = 'obsolete', last_error = ?, updated_at = ?
                WHERE action_id = ? AND status = ? AND attempts = ?
                """,
                (
                    locked_blocked_reason,
                    utc_now(),
                    args.action_id,
                    expected_status,
                    expected_attempts,
                ),
            )
            connection.commit()
            return {
                "schema": "smoky.review-conductor.dispatch.v1",
                "result": "obsolete",
                "action_id": args.action_id,
                "external_mutation_performed": False,
                "merge_dispatched": False,
            }
        claim = connection.execute(
            """
            UPDATE actions
            SET status = 'dispatching', attempts = attempts + 1,
                claim_owner = ?, claimed_at = ?, lease_expires_at = ?,
                last_error = NULL, updated_at = ?
            WHERE action_id = ? AND status = ? AND attempts = ?
            """,
            (
                getattr(args, "claim_owner", "manual-dispatch"),
                utc_now(),
                (
                    dt.datetime.now(tz=UTC)
                    + dt.timedelta(seconds=getattr(args, "claim_lease_seconds", 1800))
                ).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                utc_now(),
                args.action_id,
                expected_status,
                expected_attempts,
            ),
        )
        if claim.rowcount != 1:
            winner = connection.execute(
                "SELECT status, receipt_json FROM actions WHERE action_id = ?",
                (args.action_id,),
            ).fetchone()
            connection.rollback()
            if winner is not None and winner["status"] == "dispatched":
                return {
                    "schema": "smoky.review-conductor.dispatch.v1",
                    "result": "already_dispatched",
                    "action_id": args.action_id,
                    "receipt": json.loads(winner["receipt_json"] or "{}"),
                    "merge_dispatched": False,
                }
            raise ContractError(
                "another dispatcher won the atomic action claim; no external command was run"
            )
        connection.commit()
        receipts: list[dict[str, Any]] = []
        try:
            for index, command in enumerate(commands):
                active_head = exact_current_head(
                    connection,
                    row["repository"],
                    row["pr_number"],
                    row["base_sha"],
                    row["head_sha"],
                )
                active_blocked_reason = dispatch_block_reason(
                    active_head,
                    action_kind=row["kind"],
                    review_epoch=int(row["review_epoch"]),
                )
                if active_blocked_reason is not None:
                    connection.execute("BEGIN IMMEDIATE")
                    interrupted_receipt = {
                        "schema": "smoky.review-conductor.dispatch.v1",
                        "result": "obsolete_during_dispatch"
                        if receipts
                        else "obsolete_before_dispatch",
                        "action_id": args.action_id,
                        "kind": row["kind"],
                        "exact_head": row["head_sha"],
                        "receipts": receipts,
                        "external_mutation_performed": bool(receipts),
                        "review_enqueued": False,
                        "merge_dispatched": False,
                    }
                    connection.execute(
                        """
                        UPDATE actions
                        SET status = 'obsolete', last_error = ?, updated_at = ?
                        WHERE action_id = ? AND status = 'dispatching' AND attempts = ?
                        """,
                        (
                            active_blocked_reason,
                            utc_now(),
                            args.action_id,
                            claimed_attempts,
                        ),
                    )
                    connection.execute(
                        """
                        UPDATE actions SET receipt_json = ?, updated_at = ?
                        WHERE action_id = ? AND status = 'obsolete'
                        """,
                        (
                            canonical_json(interrupted_receipt),
                            utc_now(),
                            args.action_id,
                        ),
                    )
                    connection.commit()
                    return interrupted_receipt
                before_external_command = getattr(args, "before_external_command", None)
                if before_external_command is not None:
                    before_external_command(index, command)
                label = f"{row['kind']} adapter step {index + 1}"
                if command_environment is None:
                    # Preserve the legacy/manual dispatcher seam for existing
                    # callers and tests that intentionally inherit their process
                    # environment.
                    result = run_command(command, label)
                else:
                    result = run_command(
                        command,
                        label,
                        environment=command_environment,
                    )
                if row["kind"] == "openclaw.enqueue":
                    receipt = parse_json_receipt(result.stdout, f"{row['kind']} adapter step {index + 1}")
                    expected = "completed" if index == 0 else "queued"
                    if receipt.get("result") != expected or receipt.get("commit_sha") != row["head_sha"]:
                        raise ContractError(f"{row['kind']} adapter step {index + 1} returned mismatched exact-head receipt")
                else:
                    receipt = parse_json_receipt(result.stdout, row["kind"]) if result.stdout.strip() else {"result": "submitted"}
                receipts.append(receipt)
            receipt_payload = {
                "schema": "smoky.review-conductor.dispatch.v1",
                "result": "dispatched",
                "action_id": args.action_id,
                "kind": row["kind"],
                "exact_head": row["head_sha"],
                "receipts": receipts,
                "merge_dispatched": False,
            }
            connection.execute("BEGIN IMMEDIATE")
            final_head = exact_current_head(
                connection,
                row["repository"],
                row["pr_number"],
                row["base_sha"],
                row["head_sha"],
            )
            final_action = connection.execute(
                "SELECT status, attempts FROM actions WHERE action_id = ?",
                (args.action_id,),
            ).fetchone()
            if (
                dispatch_block_reason(
                    final_head,
                    action_kind=row["kind"],
                    review_epoch=int(row["review_epoch"]),
                )
                is not None
                or final_action is None
                or final_action["status"] != "dispatching"
                or int(final_action["attempts"]) != claimed_attempts
            ):
                historical_receipt = {
                    **receipt_payload,
                    "result": "obsolete_during_dispatch",
                    "external_mutation_performed": bool(receipts),
                }
                connection.execute(
                    """
                    UPDATE actions SET receipt_json = ?, updated_at = ?
                    WHERE action_id = ? AND status = 'obsolete'
                    """,
                    (canonical_json(historical_receipt), utc_now(), args.action_id),
                )
                connection.commit()
                return historical_receipt
            dispatched = connection.execute(
                """
                    UPDATE actions SET status = 'dispatched', receipt_json = ?,
                      lease_expires_at = NULL, updated_at = ?
                WHERE action_id = ? AND status = 'dispatching' AND attempts = ?
                """,
                (
                    canonical_json(receipt_payload),
                    utc_now(),
                    args.action_id,
                    claimed_attempts,
                ),
            )
            if dispatched.rowcount != 1:
                connection.rollback()
                raise ContractError("action dispatch completion lost its atomic claim")
            connection.commit()
            return receipt_payload
        except AuthorityDenied:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE actions SET status = 'pending', claim_owner = NULL,
                  claimed_at = NULL, lease_expires_at = NULL,
                  last_error = 'authority revoked before external command', updated_at = ?
                WHERE action_id = ? AND status = 'dispatching' AND attempts = ?
                """,
                (utc_now(), args.action_id, claimed_attempts),
            )
            connection.commit()
            raise
        except Exception as exc:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE actions SET status = 'failed', lease_expires_at = NULL,
                  last_error = ?, updated_at = ?
                WHERE action_id = ? AND status = 'dispatching' AND attempts = ?
                """,
                (str(exc)[:500], utc_now(), args.action_id, claimed_attempts),
            )
            connection.commit()
            raise
    finally:
        connection.close()


def state_projection(row: dict[str, Any]) -> dict[str, Any]:
    state = row["state"]
    if row.get("is_current", 1) in {0, False}:
        openclaw, clawsweeper = "skipped", "skipped"
    elif state == "ci_running":
        openclaw, clawsweeper = "queued", "queued"
    elif state == "ci_failed":
        openclaw, clawsweeper = "skipped", "skipped"
    elif state == "openclaw_queued":
        openclaw, clawsweeper = "queued", "queued"
    elif state == "openclaw_running":
        openclaw, clawsweeper = "in_progress", "queued"
    elif state == "openclaw_failed":
        openclaw, clawsweeper = "failure", "skipped"
    elif state == "openclaw_clean_draft":
        # Draft is a prerequisite wait, not a terminal skip: the same exact
        # tuple/epoch check must remain nonterminal when the PR becomes ready.
        openclaw, clawsweeper = "success", "queued"
    elif state == "clawsweeper_clean_draft":
        openclaw, clawsweeper = "success", "success"
    elif state == "clawsweeper_queued":
        openclaw, clawsweeper = "success", "queued"
    elif state == "clawsweeper_running":
        openclaw, clawsweeper = "success", "in_progress"
    elif state == "clawsweeper_failed":
        openclaw, clawsweeper = "success", "failure"
    elif state == "ready_for_human_merge":
        openclaw, clawsweeper = "success", "success"
    elif state in {"closed", "closed_merged"}:
        openclaw, clawsweeper = "cancelled", "cancelled"
    elif state in {"awaiting_adjudication", "repair_required", "waiting_human"}:
        if row.get("rail") == "clawsweeper":
            openclaw, clawsweeper = "success", "action_required"
        else:
            openclaw, clawsweeper = "action_required", "skipped"
    else:
        raise ContractError(f"state {state} has no truthful check projection")
    return {
        "visible_state": state,
        "checks": {
            "OpenClaw Review Rail": openclaw,
            "ClawSweeper Review Rail": clawsweeper,
        },
        "ready_for_human_label": state == "ready_for_human_merge",
        "merge_policy": "human_only",
        "merge_authorized": False,
    }


def status(args: argparse.Namespace) -> dict[str, Any]:
    config = load_config(args.config)
    if config.get("review_policy", {}).get("enabled") is False:
        return {"schema": STATUS_SCHEMA, "repository": config["repository"], "state": "inactive", "merge_authorized": False}
    connection = open_database(args.state_root, config["repository"])
    try:
        row = current_head(connection, config["repository"], args.pr_number)
        if row is None:
            return {"schema": STATUS_SCHEMA, "repository": config["repository"], "pr_number": args.pr_number, "state": "unknown", "merge_authorized": False}
        head = dict(row)
        actions = [
            {**dict(action), "payload": json.loads(action["payload_json"]), "receipt": json.loads(action["receipt_json"]) if action["receipt_json"] else None}
            for action in connection.execute(
                "SELECT * FROM actions WHERE repository = ? AND pr_number = ? AND base_sha = ? AND head_sha = ? ORDER BY review_epoch, created_at, action_id",
                (config["repository"], args.pr_number, row["base_sha"], row["head_sha"]),
            ).fetchall()
        ]
        for action in actions:
            action.pop("payload_json", None)
            action.pop("receipt_json", None)
        return {
            "schema": STATUS_SCHEMA,
            "repository": config["repository"],
            "pr_number": args.pr_number,
            "head": head,
            "actions": actions,
            "projection": state_projection(head),
            "merge_dispatched": False,
        }
    finally:
        connection.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    github = sub.add_parser("github-event", help="validate and ingest one GitHub webhook delivery")
    github.add_argument("--config", type=Path, required=True)
    github.add_argument("--state-root", type=Path, required=True)
    github.add_argument("--event-type", required=True)
    github.add_argument("--delivery-id", required=True)
    github.add_argument("--signature", required=True)
    github.add_argument("--secret-env", default="SMOKY_REVIEW_CONDUCTOR_WEBHOOK_SECRET")
    github.add_argument("--body-file", type=Path, required=True)
    github.set_defaults(func=github_delivery)

    reconcile = sub.add_parser(
        "reconcile-workflow-run",
        help="admit one exact completed CI workflow_run read-back without dispatching reviews",
    )
    reconcile.add_argument("--config", type=Path, required=True)
    reconcile.add_argument("--state-root", type=Path, required=True)
    reconcile.add_argument("--signature", required=True)
    reconcile.add_argument("--secret-env", default="SMOKY_REVIEW_CONDUCTOR_WEBHOOK_SECRET")
    reconcile.add_argument("--body-file", type=Path, required=True)
    reconcile.add_argument("--pr-number", type=int, required=True)
    reconcile.add_argument("--base-sha", required=True)
    reconcile.add_argument("--head-sha", required=True)
    reconcile.add_argument("--run-id", type=int, required=True)
    reconcile.set_defaults(func=reconcile_workflow_run_command)

    internal = sub.add_parser("internal-event", help="ingest one typed review/adjudication event")
    internal.add_argument("--config", type=Path, required=True)
    internal.add_argument("--state-root", type=Path, required=True)
    internal.add_argument("--event-file", type=Path, required=True)
    internal.set_defaults(func=internal_event)

    dispatch = sub.add_parser("dispatch-action", help="plan or apply one exact durable action and exit")
    dispatch.add_argument("--config", type=Path, required=True)
    dispatch.add_argument("--state-root", type=Path, required=True)
    dispatch.add_argument("--action-id", required=True)
    dispatch.add_argument("--source-checkout", type=Path)
    dispatch.add_argument("--apply", action="store_true")
    dispatch.add_argument("--retry", action="store_true")
    dispatch.set_defaults(func=dispatch_action)

    show = sub.add_parser("status", help="project current exact-head Review Rail state")
    show.add_argument("--config", type=Path, required=True)
    show.add_argument("--state-root", type=Path, required=True)
    show.add_argument("--pr-number", type=int, required=True)
    show.set_defaults(func=status)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        result = args.func(args)
    except ContractError as exc:
        print(f"review-conductor: {exc}", file=sys.stderr)
        return 2
    except sqlite3.Error as exc:
        print(f"review-conductor: durable state failure ({exc.__class__.__name__})", file=sys.stderr)
        return 3
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
