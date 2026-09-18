"""Trusted standalone webhook admission and exact-policy persistence.

This module composes the extracted deterministic engine with the inert trusted
admission library.  Enrollment bytes, policy transport, credentials and paths
remain service-owned inputs.  Nothing in this module enables a repository or
publishes a check by importing it.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Callable, Union

import review_conductor as core
import review_conductor_runtime as runtime
import trusted_admission as admission
from target_manifest import unique_object, validate_manifest


BINDING_TABLE_SCHEMA = "review-conductor.service-policy-binding.v1"
PROFILE_DIGEST_SCHEMA = "review-conductor.engine-profile-digest.v1"
MAX_STAGED_POLICIES = 8

# Approved policy bytes keyed by (repository, commit, sha256). Content is
# immutable under that key and is hash-verified before it is stored, so a hit
# is exactly the bytes the registry approves; the hook re-hashes and
# re-validates them inside the write transaction without any remote I/O. The
# lock guards only the dictionary (never a fetch), so the bound holds under the
# threaded HTTP server.
_STAGED_POLICIES: dict[tuple[str, str, str], bytes] = {}
_STAGED_LOCK = threading.Lock()


class PolicyNotStaged(Exception):
    """Raised inside the engine transaction when a binding needs unstaged policy bytes.

    The transaction is rolled back with nothing written; the caller stages the
    bytes for exactly the enrollment the hook resolved and re-runs the delivery.
    """

    def __init__(self, enrolled: admission.Enrollment) -> None:
        super().__init__(enrolled.repository)
        self.enrolled = enrolled


def clear_staged_policies() -> None:
    with _STAGED_LOCK:
        _STAGED_POLICIES.clear()


def _staged_key(enrolled: admission.Enrollment) -> tuple[str, str, str]:
    return (enrolled.repository, enrolled.approved_policy_commit, enrolled.approved_policy_sha256)


def staged_policy(enrolled: admission.Enrollment) -> bytes | None:
    with _STAGED_LOCK:
        return _STAGED_POLICIES.get(_staged_key(enrolled))


def stage_approved_policy(enrolled: admission.Enrollment, read_policy: PolicyReader) -> bytes:
    """Fetch the approved policy bytes outside any SQLite write transaction.

    Remote I/O never runs while the engine holds ``BEGIN IMMEDIATE``. Transient
    transport failures surface as ``RetryableIngestError`` (HTTP 503, nothing
    written) instead of a rejected delivery; every other failure stays a
    ``ServiceError``.
    """
    key = _staged_key(enrolled)
    staged = staged_policy(enrolled)
    if staged is not None:
        return staged
    try:
        raw = read_policy(enrolled.repository, enrolled.approved_policy_commit)
    except runtime.GitHubTransientError as exc:
        raise runtime.RetryableIngestError("approved policy transport failed transiently") from exc
    except Exception as exc:
        raise ServiceError("approved policy content is unavailable") from exc
    if not isinstance(raw, (bytes, bytearray)) or len(raw) > admission.MAX_BYTES:
        raise ServiceError("approved policy content is unavailable or oversized")
    raw = bytes(raw)
    if hashlib.sha256(raw).hexdigest() != enrolled.approved_policy_sha256:
        raise ServiceError("approved policy content hash does not match enrollment")
    with _STAGED_LOCK:
        # Re-check after the fetch: a concurrent delivery may have staged the same
        # key; keep its bytes (identical by hash) and never exceed the bound.
        existing = _STAGED_POLICIES.get(key)
        if existing is not None:
            return existing
        if len(_STAGED_POLICIES) >= MAX_STAGED_POLICIES:
            _STAGED_POLICIES.clear()
        _STAGED_POLICIES[key] = raw
    return raw


def _staged_reader(enrolled: admission.Enrollment, staged: bytes) -> PolicyReader:
    """A reader that only ever returns bytes already staged; it performs no I/O."""
    def read(repository: str, commit: str) -> bytes:
        if (repository, commit) != (enrolled.repository, enrolled.approved_policy_commit):
            raise ServiceError("approved policy was not staged for this delivery")
        return staged

    return read
PolicyReader = Callable[[str, str], bytes]
RegistrySource = Union[admission.Registry, Callable[[], admission.Registry]]


class ServiceError(core.ContractError):
    """A service-owned identity, enrollment or policy boundary failed closed."""


def resolve_registry(source: RegistrySource) -> admission.Registry:
    """Return the current validated registry; a provider re-reads it every time.

    Passing a provider means promotion or revocation in the service-owned registry
    is observed by the next delivery or worker tick without a restart, and a
    registry that stops validating fails every delivery and tick closed.
    """
    if isinstance(source, admission.Registry):
        return source
    if not callable(source):
        raise ServiceError("service registry source must be a Registry or a provider")
    value = source()
    if not isinstance(value, admission.Registry):
        raise ServiceError("service registry provider did not return a validated registry")
    return value


def _strict_payload(body: bytes) -> dict[str, Any]:
    try:
        value = json.loads(body.decode("utf-8"), object_pairs_hook=unique_object)
    except (UnicodeError, RecursionError, ValueError) as exc:
        raise ServiceError("GitHub webhook payload is not strict UTF-8 JSON") from exc
    return core.require_object(value, "GitHub webhook payload")


def _installation_id(payload: dict[str, Any]) -> int:
    install = core.require_object(payload.get("installation"), "payload installation")
    return core.require_positive_int(install.get("id"), "payload installation id")


def preflight_enrollment(
    config: dict[str, Any],
    registry: admission.Registry,
    payload: dict[str, Any],
    core_config: dict[str, Any] | None = None,
) -> admission.Enrollment:
    """Reject deliveries outside the running profile's enrollment before engine mutation.

    The profile resolves to exactly one enrollment (App, installation, repository
    and reviewer actors all agreeing); the delivery must then name that exact
    repository, numeric ID and installation.
    """
    enrolled = require_profile_enrolled(config, registry, core_config)
    repository = core.require_object(payload.get("repository"), "payload repository")
    name = core.require_text(repository.get("full_name"), "payload repository name", 200)
    numeric_id = core.require_positive_int(repository.get("id"), "payload repository id")
    installation_id = _installation_id(payload)
    if (
        name != enrolled.repository
        or numeric_id != enrolled.repository_id
        or installation_id != enrolled.installation_id
    ):
        raise ServiceError("GitHub delivery is outside trusted service enrollment")
    return enrolled


def _pull_request_tuple(payload: dict[str, Any]) -> tuple[int, str]:
    pull = core.require_object(payload.get("pull_request"), "payload pull_request")
    head = core.require_object(pull.get("head"), "pull_request head")
    return (
        core.require_positive_int(pull.get("number"), "pull_request number"),
        core.require_sha(head.get("sha"), "pull_request head sha"),
    )


def require_policy_matches_profile(policy: admission.AdmittedPolicy, config: dict[str, Any]) -> None:
    """Refuse to bind a policy the engine profile would not actually enforce.

    The legacy engine still reads its rules from the core profile. Until the
    admitted manifest is materialized as that profile, every policy-controlled
    field must agree exactly, otherwise a promoted manifest would be recorded as
    bound while deliveries ran under different rules.
    """
    try:
        manifest = validate_manifest(policy.manifest_bytes)
    except ValueError as exc:
        raise ServiceError("admitted policy manifest is not valid") from exc
    review_policy = core.require_object(config.get("review_policy"), "core review_policy")
    ci = core.require_object(config.get("ci"), "core ci")
    expected = {
        "repository": config.get("repository"),
        "default_branch": config.get("default_branch"),
        "ci.workflow_name": ci.get("workflow_name"),
        "ci.workflow_path": ci.get("workflow_path"),
        "review.clawsweeper_requires_ready": review_policy.get("clawsweeper_requires_ready"),
        "merge_policy": config.get("merge_policy"),
    }
    admitted = {
        "repository": manifest["repository"],
        "default_branch": manifest["default_branch"],
        "ci.workflow_name": manifest["ci"]["workflow_name"],
        "ci.workflow_path": manifest["ci"]["workflow_path"],
        "review.clawsweeper_requires_ready": manifest["review"]["clawsweeper_requires_ready"],
        "merge_policy": manifest["merge_policy"],
    }
    if (
        policy.default_branch != admitted["default_branch"]
        or policy.clawsweeper_requires_ready != admitted["review.clawsweeper_requires_ready"]
    ):
        raise ServiceError("admitted policy fields contradict its manifest bytes")
    for key, value in admitted.items():
        if type(expected[key]) is not type(value) or expected[key] != value:
            raise ServiceError("approved policy contradicts the engine profile")


def profile_policy_digest(core_config: dict[str, Any], service_config: dict[str, Any]) -> str:
    """Digest of every profile field that governs what the admitted policy reviews.

    Covers the engine fields the policy pins (repository, default branch, CI
    workflow, ClawSweeper readiness gate, merge policy) and the adapter authority fields that
    select the review producers and their destinations (ClawSweeper workflow
    id/name/path/ref/publish, adapter contract/artifact prefix, OpenClaw
    operator/transport/worktree shelf and effective Spark target/executable
    selectors). Stored with
    each binding and recompared on every tick, so a profile edited after
    admission (for example across a restart) cannot keep an old binding
    unlocking projection under rules the admitted policy never approved.
    """
    review_policy = core.require_object(core_config.get("review_policy"), "core review_policy")
    ci = core.require_object(core_config.get("ci"), "core ci")
    clawsweeper = core.require_object(core_config.get("clawsweeper"), "core clawsweeper")
    openclaw = core.require_object(core_config.get("openclaw"), "core openclaw")
    spark = service_config.get("spark")
    spark_target = None
    spark_executables = None
    if spark is not None:
        spark = core.require_object(spark, "service spark")
        spark_target = core.require_text(
            spark.get("target"),
            "service spark target",
            255,
        )
        spark_executables = {
            key: core.require_text(spark.get(key), f"service spark {key}", 4096)
            for key in ("smoky_path", "ssh_path", "scp_path")
        }
    adapter = service_config.get("adapter")
    if adapter is not None:
        adapter = core.require_object(adapter, "service adapter")
    canonical = json.dumps(
        {
            "schema": PROFILE_DIGEST_SCHEMA,
            "repository": core_config.get("repository"),
            "repository_id": core_config.get("repository_id"),
            "default_branch": core_config.get("default_branch"),
            "ci": {"workflow_name": ci.get("workflow_name"), "workflow_path": ci.get("workflow_path")},
            "clawsweeper": {
                key: clawsweeper.get(key)
                for key in ("workflow_id", "workflow_name", "workflow_path", "ref", "publish")
            },
            "adapter": None if adapter is None else {
                "contract": adapter.get("contract"), "artifact_prefix": adapter.get("artifact_prefix")
            },
            "openclaw": {
                key: openclaw.get(key) for key in ("operator_id", "transport", "remote_worktree_shelf", "exact_tuple_contract")
            },
            "spark_target": spark_target,
            "spark_executables": spark_executables,
            "clawsweeper_requires_ready": review_policy.get("clawsweeper_requires_ready"),
            "merge_policy": core_config.get("merge_policy"),
        },
        sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _ensure_binding_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS service_policy_bindings (
          repository TEXT NOT NULL,
          pr_number INTEGER NOT NULL,
          base_sha TEXT NOT NULL,
          head_sha TEXT NOT NULL,
          review_epoch INTEGER NOT NULL,
          app_id INTEGER NOT NULL,
          installation_id INTEGER NOT NULL,
          policy_commit TEXT NOT NULL,
          policy_sha256 TEXT NOT NULL,
          policy_id TEXT NOT NULL,
          binding_id TEXT NOT NULL,
          reviewer_openclaw TEXT NOT NULL,
          reviewer_clawsweeper TEXT NOT NULL,
          profile_digest TEXT NOT NULL,
          created_at TEXT NOT NULL,
          PRIMARY KEY (repository, pr_number, base_sha, head_sha, review_epoch)
        )
        """
    )
    columns = {
        row["name"] if isinstance(row, sqlite3.Row) else row[1]
        for row in connection.execute("PRAGMA table_info(service_policy_bindings)")
    }
    if "profile_digest" not in columns:
        # PR #3 databases predate profile-digest currency. Preserve their rows as
        # untrusted until a new exact tuple is admitted under the current profile.
        connection.execute(
            "ALTER TABLE service_policy_bindings ADD COLUMN profile_digest TEXT"
        )


def _persist_binding(
    connection: sqlite3.Connection,
    value: admission.Admission,
    enrolled: admission.Enrollment,
    profile_digest: str,
) -> None:
    _ensure_binding_table(connection)
    identity = (
        value.review.repository,
        value.review.pr_number,
        value.review.base_sha,
        value.review.head_sha,
        value.review.review_epoch,
    )
    prior = connection.execute(
        """
        SELECT app_id, installation_id, policy_commit, policy_sha256, policy_id, binding_id,
               reviewer_openclaw, reviewer_clawsweeper, profile_digest
        FROM service_policy_bindings
        WHERE repository=? AND pr_number=? AND base_sha=? AND head_sha=? AND review_epoch=?
        """,
        identity,
    ).fetchone()
    expected = (
        value.app_id,
        value.installation_id,
        value.policy.commit,
        value.policy.sha256,
        value.policy.policy_id,
        value.binding_id,
        enrolled.reviewer_openclaw,
        enrolled.reviewer_clawsweeper,
        profile_digest,
    )
    if prior is not None and tuple(prior) != expected:
        raise ServiceError("exact review tuple already has a conflicting policy binding")
    connection.execute(
        """
        INSERT OR IGNORE INTO service_policy_bindings(
          repository, pr_number, base_sha, head_sha, review_epoch,
          app_id, installation_id, policy_commit, policy_sha256,
          policy_id, binding_id, reviewer_openclaw, reviewer_clawsweeper,
          profile_digest, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (*identity, *expected, core.utc_now()),
    )


def _admission_hook(
    registry_source: RegistrySource,
    service_config: dict[str, Any],
) -> Callable[[sqlite3.Connection, dict[str, Any], str, dict[str, Any], dict[str, Any]], dict[str, Any] | None]:
    def hook(
        connection: sqlite3.Connection,
        config: dict[str, Any],
        event_type: str,
        payload: dict[str, Any],
        outcome: dict[str, Any],
    ) -> dict[str, Any] | None:
        # Only the accepted pull_request delivery that established a head may bind it.
        # workflow_run and issue_comment deliveries never create bindings: their
        # head_sha is engine evidence, not admission, and the head they refer to is
        # either already bound by its own delivery or must not become bound by them.
        if (
            event_type != "pull_request"
            or outcome.get("result") != "accepted"
            or payload.get("action") == "closed"
        ):
            return None
        # Re-read the registry immediately before admission, inside the same
        # transaction that persists the binding: a promotion or revocation after
        # preflight is observed here, and the delivery must still be within the
        # enrollment that ``config`` (the engine-reloaded profile) resolves to.
        registry = resolve_registry(registry_source)
        enrolled = preflight_enrollment(service_config, registry, payload, config)
        pr_number, event_head = _pull_request_tuple(payload)
        row = core.current_head(connection, enrolled.repository, pr_number)
        if row is None:
            raise ServiceError("accepted delivery has no current exact review tuple")
        if row["head_sha"] != event_head:
            raise ServiceError("accepted delivery head is not the current exact review tuple")
        request = {
            "repository": enrolled.repository,
            "repository_id": enrolled.repository_id,
            "app_id": enrolled.app_id,
            "installation_id": enrolled.installation_id,
            "pr_number": pr_number,
            "base_sha": row["base_sha"],
            "head_sha": row["head_sha"],
            "review_epoch": row["review_epoch"],
            "policy_commit": enrolled.approved_policy_commit,
        }
        # The engine has classified this delivery as a binding candidate. Only now
        # are policy bytes required; if they are not staged, unwind the transaction
        # (nothing written) so the caller can fetch them outside the lock.
        staged = staged_policy(enrolled)
        if staged is None:
            raise PolicyNotStaged(enrolled)
        try:
            bound = admission.admit(registry, request, _staged_reader(enrolled, staged))
        except admission.AdmissionError as exc:
            raise ServiceError("approved policy could not be bound to the exact review tuple") from exc
        require_policy_matches_profile(bound.policy, config)
        _persist_binding(connection, bound, enrolled, profile_policy_digest(config, service_config))
        return {
            "schema": BINDING_TABLE_SCHEMA,
            "binding_id": bound.binding_id,
            "policy_id": bound.policy.policy_id,
        }

    return hook


def ingest_service_delivery(
    *,
    config_path: Path,
    state_root: Path,
    event_type: str,
    delivery_id: str,
    signature: str,
    body: bytes,
    secret: str,
    registry: RegistrySource,
    read_policy: PolicyReader,
    service_config: dict[str, Any],
) -> dict[str, Any]:
    """Authenticate, admit and ingest one GitHub App delivery atomically."""
    # Authentication must precede JSON parsing, registry resolution, lookup and policy I/O.
    core.verify_github_signature(body, signature, secret)
    payload = _strict_payload(body)
    core_config = core.load_config(config_path)
    registry_source = registry
    # Early rejection only; the admission hook re-resolves the registry inside
    # the engine transaction and is the authority for what gets persisted.
    registry = resolve_registry(registry_source)
    enrolled = preflight_enrollment(service_config, registry, payload, core_config)
    if (
        core_config["repository"] != enrolled.repository
        or core_config["repository_id"] != enrolled.repository_id
    ):
        raise ServiceError("core profile contradicts trusted service enrollment")
    # Policy transport runs only when the engine, inside its transaction, has
    # classified the delivery as a new non-closed binding candidate; closed,
    # duplicate and stale deliveries never touch the policy dependency. The
    # fetch itself happens after the transaction has been rolled back, for the
    # enrollment the hook resolved at that moment, and the delivery is re-run;
    # a promotion observed on the re-run stages once more before giving up.
    for _attempt in range(3):
        try:
            return core.ingest_github_delivery(
                config_path=config_path,
                state_root=state_root,
                event_type=event_type,
                delivery_id=delivery_id,
                signature=signature,
                body=body,
                secret=secret,
                admission_hook=_admission_hook(registry_source, service_config),
            )
        except PolicyNotStaged as pending:
            stage_approved_policy(pending.enrolled, read_policy)
    raise runtime.RetryableIngestError("approved policy staging raced registry or cache changes")


def build_service_http_handler(
    config: dict[str, Any],
    *,
    secret: str,
    registry: RegistrySource,
    read_policy: PolicyReader,
):
    """Build the loopback handler placed behind the separately owned HTTPS edge."""
    def ingestor(**kwargs: Any) -> dict[str, Any]:
        return ingest_service_delivery(
            **kwargs, registry=registry, read_policy=read_policy,
            service_config=config,
        )

    return runtime.build_http_handler(config, secret, ingestor=ingestor)


def binding_for_current_head(
    connection: sqlite3.Connection,
    registry: admission.Registry,
    repository: str,
    pr_number: int,
    core_config: dict[str, Any],
    service_config: dict[str, Any],
) -> dict[str, Any] | None:
    """Return the current binding only while every authority it was admitted under holds.

    The registry must still approve the same policy commit/hash and name the same
    reviewer actors, and the engine profile must still digest to what it was at
    admission; otherwise the tuple must be re-admitted under a new head/epoch.
    """
    head = core.current_head(connection, repository, pr_number)
    if head is None:
        return None
    # Read-only: the gate may run while a worker phase holds the write lock, so
    # it never creates the table; a database without it simply has no bindings.
    if connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='service_policy_bindings'"
    ).fetchone() is None:
        return None
    columns = {
        row["name"] if isinstance(row, sqlite3.Row) else row[1]
        for row in connection.execute("PRAGMA table_info(service_policy_bindings)")
    }
    if "profile_digest" not in columns:
        return None
    row = connection.execute(
        """
        SELECT * FROM service_policy_bindings
        WHERE repository=? AND pr_number=? AND base_sha=? AND head_sha=? AND review_epoch=?
        """,
        (repository, pr_number, head["base_sha"], head["head_sha"], head["review_epoch"]),
    ).fetchone()
    if row is None:
        return None
    try:
        enrolled = registry.lookup(
            row["repository"], admission.INITIAL_ENROLLMENT_SCOPE[row["repository"]],
            row["app_id"], row["installation_id"],
        )
    except (admission.AdmissionError, KeyError):
        return None
    if (
        enrolled.approved_policy_commit != row["policy_commit"]
        or enrolled.approved_policy_sha256 != row["policy_sha256"]
    ):
        return None
    if (
        enrolled.reviewer_openclaw != row["reviewer_openclaw"]
        or enrolled.reviewer_clawsweeper != row["reviewer_clawsweeper"]
    ):
        return None
    if profile_policy_digest(core_config, service_config) != row["profile_digest"]:
        return None
    return dict(row)


def require_profile_enrolled(
    config: dict[str, Any],
    registry: admission.Registry,
    core_config: dict[str, Any] | None = None,
) -> admission.Enrollment:
    """The runtime profile must be the registry's enrollment, reviewers included.

    The registry, not the profile, is the authority for App/installation/
    repository identity and for the OpenClaw/ClawSweeper reviewer actors the
    engine trusts. A profile that names different reviewers cannot serve, tick
    or accept deliveries.
    """
    app = core.require_object(config.get("github_app"), "service GitHub App config")
    try:
        enrolled = registry.lookup(
            app.get("repository"), app.get("repository_id"), app.get("app_id"), app.get("installation_id")
        )
    except admission.AdmissionError as exc:
        raise ServiceError("runtime profile is not enrolled in the service registry") from exc
    if core_config is None:
        core_config = core.load_config(Path(config["core_config"]))
    if (
        core_config.get("repository") != enrolled.repository
        or core_config.get("repository_id") != enrolled.repository_id
    ):
        raise ServiceError("runtime core repository identity contradicts service enrollment")
    review_policy = core.require_object(core_config.get("review_policy"), "core review_policy")
    # A profile deactivated after startup must fail every provider read, delivery
    # and tick closed, not merely stop admitting new heads.
    if review_policy.get("enabled") is not True:
        raise ServiceError("runtime core profile is not enabled")
    if review_policy.get("reviewers") != enrolled.reviewers:
        raise ServiceError("runtime reviewer identities contradict service enrollment")
    return enrolled


def _gate_connection(state_root: Path) -> sqlite3.Connection | None:
    """A read-only view of the engine database for the admission gate.

    The gate also runs from the per-side-effect authority guard while a worker
    phase may hold the engine's write transaction (WAL mode), so it must never
    take the write lock or migrate; a database that does not exist yet has no
    live heads to gate.
    """
    database = Path(state_root) / "review-conductor.sqlite3"
    try:
        database.stat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ServiceError("admission-gate database is unavailable") from exc
    try:
        connection = sqlite3.connect(
            f"file:{database.as_posix()}?mode=ro", uri=True, timeout=10
        )
    except sqlite3.Error as exc:
        # The file may have disappeared or become unreadable after the stat;
        # that race is a failed gate, not an empty-state success.
        raise ServiceError("admission-gate database could not be opened read-only") from exc
    connection.row_factory = sqlite3.Row
    return connection


def require_current_bindings(config: dict[str, Any], registry: RegistrySource) -> None:
    """Block every worker/projection tick if any live head lacks current policy."""
    registry = resolve_registry(registry)
    core_config = core.load_config(Path(config["core_config"]))
    require_profile_enrolled(config, registry, core_config)
    connection = _gate_connection(Path(config["paths"]["state_root"]))
    if connection is None:
        return
    try:
        rows = connection.execute(
            """
            SELECT repository, pr_number FROM heads
            WHERE repository=? AND is_current=1 AND state NOT IN ('closed', 'closed_merged')
            ORDER BY pr_number
            """,
            (config["github_app"]["repository"],),
        ).fetchall()
        for row in rows:
            if binding_for_current_head(
                connection, registry, row["repository"], row["pr_number"], core_config, config
            ) is None:
                raise ServiceError("current review tuple lacks a current approved-policy binding")
    finally:
        connection.close()


def require_current_binding(
    connection: sqlite3.Connection,
    config: dict[str, Any],
    registry: RegistrySource,
    pr_number: int,
) -> dict[str, Any]:
    """Require one live tuple's current authority inside its mutation transaction.

    Standalone maintenance operations use the same transaction for this check and
    their state change. The registry is re-read here, after ``BEGIN IMMEDIATE``,
    so a profile or enrollment revocation observed before the write fails closed.
    """
    registry = resolve_registry(registry)
    core_config = core.load_config(Path(config["core_config"]))
    enrolled = require_profile_enrolled(config, registry, core_config)
    repository = enrolled.repository
    head = core.current_head(connection, repository, pr_number)
    if head is None or head["state"] in {"closed", "closed_merged"}:
        raise ServiceError("maintenance target has no live current review tuple")
    binding = binding_for_current_head(
        connection, registry, repository, pr_number, core_config, config
    )
    if binding is None:
        raise ServiceError("maintenance target lacks a current approved-policy binding")
    return binding


def require_exact_current_binding(
    config: dict[str, Any],
    registry: RegistrySource,
    authority: Any,
) -> dict[str, Any]:
    """Require the exact tuple owning one imminent external side effect.

    A newer valid head must not authorize work selected for an older one. Each
    invocation resolves the registry and opens a fresh read-only database view,
    then matches repository, PR, base, head and review epoch before accepting
    the tuple's still-current policy binding.
    """
    exact = runtime.tuple_authority(authority)
    registry = resolve_registry(registry)
    core_config = core.load_config(Path(config["core_config"]))
    enrolled = require_profile_enrolled(config, registry, core_config)
    if exact["repository"] != enrolled.repository:
        raise ServiceError("side-effect tuple repository contradicts service enrollment")
    connection = _gate_connection(Path(config["paths"]["state_root"]))
    if connection is None:
        raise ServiceError("side-effect tuple has no admission-gate database")
    try:
        # Keep the head and binding reads on one snapshot. A promotion observed
        # between two autocommit SELECTs must not pair tuple A with tuple B's
        # otherwise-valid binding.
        connection.execute("BEGIN")
        head = core.exact_current_head(
            connection,
            exact["repository"],
            exact["pr_number"],
            exact["base_sha"],
            exact["head_sha"],
        )
        if (
            head is None
            or head["state"] in {"closed", "closed_merged"}
            or int(head["review_epoch"]) != exact["review_epoch"]
        ):
            raise ServiceError("side-effect tuple is not the live current review tuple")
        binding = binding_for_current_head(
            connection,
            registry,
            exact["repository"],
            exact["pr_number"],
            core_config,
            config,
        )
        if binding is None:
            raise ServiceError("side-effect tuple lacks a current approved-policy binding")
        if any(binding[key] != exact[key] for key in exact):
            raise ServiceError("approved-policy binding contradicts side-effect tuple")
        return binding
    finally:
        connection.close()


def run_service_tick(
    config: dict[str, Any],
    registry: RegistrySource,
    client: Any,
    notifier: Any,
    *,
    dry_run: bool,
) -> dict[str, Any]:
    """Run the legacy-compatible worker only behind current admission bindings.

    The opening gate is re-run before every mutating GitHub call the tick makes
    (check creation/update, ClawSweeper dispatch) through the client's authority
    guard, so a registry revocation or profile edit after the gate stops the
    remainder of the tick instead of letting it publish under stale authority.
    """
    require_current_bindings(config, registry)
    import review_conductor_userland as userland

    if not dry_run:
        install = getattr(client, "set_authority_guard", None)
        assertion = getattr(client, "assert_authority", None)
        if not callable(install) or not callable(assertion):
            raise ServiceError(
                "tick client must install and assert the admission authority guard"
            )
        def authority_guard(
            _method: str,
            _path: str,
            authority: dict[str, Any] | None,
        ) -> None:
            # Explicit stale-check cleanup and unbound operator alerts are
            # repository-level maintenance. Every current review/check action
            # supplies an exact tuple and must still own that tuple here.
            if authority is None:
                require_current_bindings(config, registry)
            else:
                require_current_bindings(config, registry)
                require_exact_current_binding(config, registry, authority)

        install(authority_guard)
    return userland.run_tick(config, client, notifier, dry_run=dry_run)
