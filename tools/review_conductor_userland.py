#!/usr/bin/env python3
"""User-owned Review Conductor runtime for one boring DinkusKit PR loop."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import threading
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping


TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
import review_conductor as core  # noqa: E402
import review_conductor_runtime as runtime  # noqa: E402
import review_conductor_profiles as profiles  # noqa: E402
import review_result_projection as projection  # noqa: E402
import orchestration_outcome as orchestration  # noqa: E402


USERLAND_SCHEMA = "smoky.review-conductor.userland.v1"
NOTIFICATION_SCHEMA = "smoky.review-conductor.notification.v1"
DEFAULT_CONFIG = ROOT / "contracts/review-conductor/dinkuskit-blocks-userland.json"
# Engine terminal states. Notification eligibility is decided by
# orchestration_outcome, not by membership in this set.
TERMINAL_STATES = {
    "ci_failed",
    "awaiting_adjudication",
    "repair_required",
    "waiting_human",
    "openclaw_failed",
    "clawsweeper_failed",
    "ready_for_human_merge",
}
READY_OVERALL_TIERS = {"S", "A", "B"}
READY_PROOF_STATUSES = {"sufficient"}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
FRONTMATTER_RE = re.compile(r"\A---\n(?P<body>.*?)\n---(?:\n|\Z)", re.DOTALL)
FINDING_RE = re.compile(r"^- \*\*\[P[0-3]\]", re.MULTILINE)


class UserlandError(core.ContractError):
    """Userland runtime input or state violated the fixed pilot contract."""


class NotificationUnavailable(UserlandError):
    """A configured notification destination is not ready yet."""


def home_path(home: Path, value: Any, label: str) -> Path:
    text = core.require_text(value, label, 700)
    relative = PurePosixPath(text)
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        raise UserlandError(f"{label} must be a traversal-free path relative to HOME")
    return home.joinpath(*relative.parts)


def source_path(source_root: Path, value: Any, label: str) -> Path:
    text = core.require_text(value, label, 700)
    relative = PurePosixPath(text)
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        raise UserlandError(f"{label} must be a traversal-free source-relative path")
    path = source_root.joinpath(*relative.parts)
    try:
        path.relative_to(source_root)
    except ValueError as exc:
        raise UserlandError(f"{label} escapes the source root") from exc
    return path


def require_env_name(value: Any, label: str) -> str:
    name = core.require_text(value, label, 128)
    if re.fullmatch(r"[A-Z][A-Z0-9_]{2,127}", name) is None:
        raise UserlandError(f"{label} must be an uppercase environment variable name")
    return name


def load_config(
    path: Path = DEFAULT_CONFIG,
    *,
    home: Path | None = None,
    source_root: Path | None = None,
    _check_peers: bool = True,
) -> dict[str, Any]:
    config = core.read_json(path, "Review Conductor userland config")
    core.require_exact_keys(
        config,
        {
            "schema",
            "mode",
            "legacy_cp1_route",
            "core_config",
            "paths",
            "ingress",
            "worker",
            "spark",
            "github_app",
            "projection",
            "credentials",
            "onepassword",
            "tunnel",
            "notifications",
        },
        {"profile_id", "enrollment", "adapter"},
        "Review Conductor userland config",
    )
    generalized = config["schema"] == profiles.SCHEMA
    if config["schema"] not in {USERLAND_SCHEMA, profiles.SCHEMA}:
        raise UserlandError("userland config schema is unsupported")
    if config["mode"] != "cp1-current-user":
        raise UserlandError("userland mode must remain cp1-current-user")
    if config["legacy_cp1_route"] != "disabled":
        raise UserlandError("the legacy root/custom-UID route must remain disabled")
    resolved_home = (home or Path.home()).resolve(strict=True)
    resolved_source = (source_root or ROOT).resolve(strict=True)
    if resolved_home.is_symlink() or resolved_source.is_symlink():
        raise UserlandError("HOME and source root must resolve to real directories")
    core_config = source_path(resolved_source, config["core_config"], "core_config")
    core_profile = core.load_config(core_config)
    if generalized:
        profiles.validate_profile(config, core_profile)

    paths = core.require_object(config["paths"], "paths")
    if generalized:
        if "blocks_checkout" in paths or "source_checkout" not in paths:
            raise UserlandError("generalized profiles require paths.source_checkout")
        paths = {**paths, "blocks_checkout": paths["source_checkout"]}
        del paths["source_checkout"]
    core.require_exact_keys(
        paths,
        {
            "state_root",
            "proof_root",
            "blocks_checkout",
            "openclaw_terminal_inbox",
            "clawsweeper_terminal_inbox",
        },
        set(),
        "paths",
    )
    resolved_paths = {
        key: home_path(resolved_home, value, f"paths.{key}")
        for key, value in paths.items()
    }
    if resolved_paths["state_root"] == resolved_paths["blocks_checkout"]:
        raise UserlandError("state root and Blocks checkout must be distinct")
    if resolved_paths["proof_root"].is_relative_to(resolved_paths["blocks_checkout"]):
        raise UserlandError("proof root may not be inside the Blocks checkout")

    ingress = core.require_object(config["ingress"], "ingress")
    core.require_exact_keys(
        ingress,
        {
            "bind_host",
            "bind_port",
            "path",
            "public_hostname",
            "max_body_bytes",
            "request_timeout_seconds",
        },
        set(),
        "ingress",
    )
    if ingress["bind_host"] not in {"127.0.0.1", "::1"}:
        raise UserlandError("userland ingress must bind only to loopback")
    port = core.require_positive_int(ingress["bind_port"], "ingress.bind_port")
    if port > 65535:
        raise UserlandError("ingress.bind_port is outside the TCP port range")
    if ingress["path"] != "/github/webhook":
        raise UserlandError("ingress.path must remain /github/webhook")
    core.require_text(ingress["public_hostname"], "ingress.public_hostname", 253)
    body_limit = core.require_positive_int(
        ingress["max_body_bytes"], "ingress.max_body_bytes"
    )
    if body_limit > 2 * 1024 * 1024:
        raise UserlandError("ingress body limit may not exceed 2 MiB")
    timeout = core.require_positive_int(
        ingress["request_timeout_seconds"], "ingress.request_timeout_seconds"
    )
    if timeout > 30:
        raise UserlandError("ingress timeout may not exceed 30 seconds")

    worker = core.require_object(config["worker"], "worker")
    core.require_exact_keys(
        worker,
        {
            "claim_lease_seconds",
            "max_actions_per_tick",
            "tick_seconds",
        },
        set(),
        "worker",
    )
    lease = core.require_positive_int(
        worker["claim_lease_seconds"], "worker.claim_lease_seconds"
    )
    maximum = core.require_positive_int(
        worker["max_actions_per_tick"], "worker.max_actions_per_tick"
    )
    tick = core.require_positive_int(worker["tick_seconds"], "worker.tick_seconds")
    if not 30 <= lease <= 3600 or maximum > 100 or not 2 <= tick <= 300:
        raise UserlandError("worker bounds are outside the fixed pilot envelope")

    spark = core.require_object(config["spark"], "spark")
    core.require_exact_keys(
        spark,
        {"target", "transport", "smoky_path", "ssh_path", "scp_path"},
        set(),
        "spark",
    )
    if spark["target"] not in {"spark-2", "spark-2.swarm"}:
        raise UserlandError("spark.target must remain Spark-2")
    if spark["transport"] != "bundle":
        raise UserlandError("Spark transport must remain bundle")
    smoky_path = source_path(resolved_source, spark["smoky_path"], "spark.smoky_path")
    ssh_path = Path(core.require_text(spark["ssh_path"], "spark.ssh_path", 300))
    scp_path = Path(core.require_text(spark["scp_path"], "spark.scp_path", 300))
    if not ssh_path.is_absolute() or not scp_path.is_absolute():
        raise UserlandError("Spark transport commands must be absolute")

    app = core.require_object(config["github_app"], "github_app")
    core.require_exact_keys(
        app,
        {
            "api_base",
            "app_id",
            "installation_id",
            "repository",
            "repository_id",
            "permissions",
            "denied_permissions",
            "events",
        },
        set(),
        "github_app",
    )
    if app["api_base"] != "https://api.github.com":
        raise UserlandError("GitHub API base must remain https://api.github.com")
    for key in ("app_id", "installation_id"):
        if not (generalized and not config["enrollment"]["enabled"] and app[key] is None):
            core.require_positive_int(app[key], f"github_app.{key}")
    if not generalized and (app["repository"] != "dinkuskit/blocks" or app["repository_id"] != 1306882611):
        raise UserlandError("legacy GitHub App scope must remain exact dinkuskit/blocks")
    if app["repository"] != core_profile["repository"] or app["repository_id"] != core_profile["repository_id"]:
        raise UserlandError("GitHub App scope must match the exact core repository")
    expected_permissions = (
        runtime.STANDALONE_APP_PERMISSIONS if generalized else runtime.APP_PERMISSIONS
    )
    expected_denied_permissions = (
        runtime.STANDALONE_DENIED_PERMISSIONS if generalized else runtime.DENIED_PERMISSIONS
    )
    if app["permissions"] != expected_permissions:
        raise UserlandError("GitHub App permissions differ from the closed allowlist")
    if set(app["denied_permissions"]) != expected_denied_permissions:
        raise UserlandError("GitHub App denied permissions are incomplete")
    if app["events"] != runtime.APP_EVENTS:
        raise UserlandError("GitHub App events must remain pull_request and workflow_run")
    projection = core.require_object(config["projection"], "projection")
    core.require_exact_keys(
        projection,
        {"check_names", "ready_label"},
        set(),
        "projection",
    )
    if projection["check_names"] != list(runtime.CHECK_NAMES):
        raise UserlandError("projection check names differ from the fixed contract")
    if projection["ready_label"] != runtime.READY_LABEL:
        raise UserlandError("projection ready label differs from the fixed contract")

    credentials = core.require_object(config["credentials"], "credentials")
    core.require_exact_keys(
        credentials,
        {"webhook_secret_fd_env", "github_private_key_fd_env"},
        set(),
        "credentials",
    )
    webhook_fd_env = require_env_name(
        credentials["webhook_secret_fd_env"], "credentials.webhook_secret_fd_env"
    )
    github_fd_env = require_env_name(
        credentials["github_private_key_fd_env"],
        "credentials.github_private_key_fd_env",
    )
    if webhook_fd_env == github_fd_env:
        raise UserlandError("webhook and GitHub credentials must use distinct descriptors")

    onepassword = core.require_object(config["onepassword"], "onepassword")
    core.require_exact_keys(
        onepassword,
        {"op_path", "bootstrap_root", "recovery_vault", "domains"},
        set(),
        "onepassword",
    )
    op_path = Path(core.require_text(onepassword["op_path"], "onepassword.op_path", 300))
    if not op_path.is_absolute():
        raise UserlandError("onepassword.op_path must be absolute")
    bootstrap_root = home_path(
        resolved_home, onepassword["bootstrap_root"], "onepassword.bootstrap_root"
    )
    recovery_vault = core.require_text(
        onepassword["recovery_vault"], "onepassword.recovery_vault", 200
    )
    expected_domains = {
        "review-conductor.blocks.webhook-verify": (
            "Blocks Webhook Service Account Recovery",
            "Review Conductor Blocks Webhook Runtime",
            "Review Conductor Blocks Webhook",
            "secret",
            "webhook-verify.token",
        ),
        "review-conductor.blocks.github-installation": (
            "Blocks GitHub App Service Account Recovery",
            "Review Conductor Blocks GitHub App Runtime",
            "Review Conductor Blocks GitHub App",
            "private-key.pem",
            "github-installation.token",
        ),
        "review-conductor.blocks.cloudflare-tunnel": (
            "Blocks Cloudflare Tunnel Service Account Recovery",
            "Smoky Review Conductor Blocks Cloudflare Tunnel",
            "DinkusKit Blocks Cloudflare Tunnel",
            "connector-token",
            "cloudflare-tunnel.token",
        ),
    }
    if generalized:
        expected_domains = {capability: None for capability in profiles.capabilities(config)}
    domains = onepassword["domains"]
    if not isinstance(domains, list) or len(domains) != len(expected_domains):
        raise UserlandError("onepassword domains must contain the three fixed capabilities")
    resolved_domains: dict[str, dict[str, str]] = {}
    for raw_domain in domains:
        domain = core.require_object(raw_domain, "onepassword domain")
        core.require_exact_keys(
            domain,
            {
                "capability_id",
                "recovery_item",
                "recovery_field",
                "runtime_vault",
                "runtime_item",
                "runtime_field",
                "bootstrap_file",
            },
            set(),
            "onepassword domain",
        )
        capability = core.require_text(
            domain["capability_id"], "onepassword capability_id", 100
        )
        expected = expected_domains.get(capability)
        observed = (
            domain["recovery_item"],
            domain["runtime_vault"],
            domain["runtime_item"],
            domain["runtime_field"],
            domain["bootstrap_file"],
        )
        if (not generalized and (expected is None or observed != expected)) or capability not in expected_domains or domain["recovery_field"] != "token":
            raise UserlandError("onepassword domain differs from the fixed selector contract")
        if generalized:
            for key in ("recovery_item", "runtime_vault", "runtime_item", "runtime_field", "bootstrap_file"):
                value = core.require_text(domain[key], f"domain.{key}", 200)
                if "/" in value or "\\" in value or value in {".", ".."}:
                    raise UserlandError("unsafe credential selector segment")
        if capability in resolved_domains:
            raise UserlandError("onepassword capability is duplicated")
        resolved_domains[capability] = {
            **domain,
            "bootstrap_file": str(bootstrap_root / domain["bootstrap_file"]),
            "recovery_reference": (
                f"op://{recovery_vault}/{domain['recovery_item']}/{domain['recovery_field']}"
            ),
            "runtime_reference": (
                f"op://{domain['runtime_vault']}/{domain['runtime_item']}/{domain['runtime_field']}"
            ),
        }
    if generalized:
        for key in ("runtime_vault", "bootstrap_file", "recovery_item"):
            if len({domain[key] for domain in resolved_domains.values()}) != len(resolved_domains):
                raise UserlandError("profile credential domains must remain isolated")
    if set(resolved_domains) != set(expected_domains):
        raise UserlandError("onepassword capability set is incomplete")

    tunnel = core.require_object(config["tunnel"], "tunnel")
    core.require_exact_keys(
        tunnel,
        {"cloudflared_path", "tunnel_id", "tunnel_name"},
        set(),
        "tunnel",
    )
    cloudflared_path = Path(
        core.require_text(tunnel["cloudflared_path"], "tunnel.cloudflared_path", 300)
    )
    if not cloudflared_path.is_absolute():
        raise UserlandError("tunnel.cloudflared_path must be absolute")
    if not generalized and tunnel["tunnel_id"] != "9dd6e25e-3f9e-4c6b-b2f0-2bbdd1deed64":
        raise UserlandError("tunnel ID differs from the fixed Blocks connector")
    if not generalized and tunnel["tunnel_name"] != "smoky-blocks-review-conductor-1306882611":
        raise UserlandError("tunnel name differs from the fixed Blocks connector")

    notifications = core.require_object(config["notifications"], "notifications")
    core.require_exact_keys(
        notifications,
        {
            "openclaw_path",
            "agent_id",
            "session_key",
            "discord_target_env",
            "signal_target_env",
            "ready_overall_tiers",
            "ready_proof_statuses",
        },
        set(),
        "notifications",
    )
    openclaw_path = Path(
        core.require_text(notifications["openclaw_path"], "notifications.openclaw_path", 300)
    )
    if not openclaw_path.is_absolute():
        raise UserlandError("notifications.openclaw_path must be absolute")
    if notifications["agent_id"] != "main":
        raise UserlandError("notifications must use the live Swarm main agent")
    if notifications["session_key"] != (f"agent:main:review-conductor:{config['profile_id']}" if generalized else "agent:main:review-conductor"):
        raise UserlandError("notification session key differs from the fixed conductor session")
    discord_target_env = require_env_name(
        notifications["discord_target_env"], "notifications.discord_target_env"
    )
    signal_target_env = require_env_name(
        notifications["signal_target_env"], "notifications.signal_target_env"
    )
    if set(notifications["ready_overall_tiers"]) != READY_OVERALL_TIERS:
        raise UserlandError("ready overall tiers must remain Platinum Hermit or better")
    if set(notifications["ready_proof_statuses"]) != READY_PROOF_STATUSES:
        raise UserlandError("ready proof status must remain sufficient")

    result = {
        "schema": config["schema"],
        "mode": config["mode"],
        "legacy_cp1_route": config["legacy_cp1_route"],
        "source_root": str(resolved_source),
        "home": str(resolved_home),
        "core_config": str(core_config),
        "paths": {
            "state_root": str(resolved_paths["state_root"]),
            "proof_root": str(resolved_paths["proof_root"]),
            "blocks_checkout": str(resolved_paths["blocks_checkout"]),
            "selector_registry": str(resolved_home / ".config/smoky/review-conductor/selectors.json"),
            "action_wake": str(resolved_paths["state_root"] / "wake/action.json"),
            "projection_wake": str(resolved_paths["state_root"] / "wake/projection.json"),
        },
        "ingress": ingress,
        "worker": {
            "claim_lease_seconds": lease,
            "recovery_tick_seconds": max(30, tick),
            "max_actions_per_wake": maximum,
            "tick_seconds": tick,
        },
        "spark": {
            "target": spark["target"],
            "transport": spark["transport"],
            "smoky_path": str(smoky_path),
            "ssh_path": str(ssh_path),
            "scp_path": str(scp_path),
            "terminal_inbox": str(resolved_paths["openclaw_terminal_inbox"]),
        },
        "clawsweeper_bridge": {
            "terminal_inbox": str(resolved_paths["clawsweeper_terminal_inbox"])
        },
        "github_app": app,
        "projection": config["projection"],
        "credentials": {
            "webhook_secret_fd_env": webhook_fd_env,
            "github_private_key_fd_env": github_fd_env,
        },
        "onepassword": {
            "op_path": str(op_path),
            "bootstrap_root": str(bootstrap_root),
            "recovery_vault": recovery_vault,
            "domains": resolved_domains,
        },
        "tunnel": {
            "cloudflared_path": str(cloudflared_path),
            "tunnel_id": tunnel["tunnel_id"],
            "tunnel_name": tunnel["tunnel_name"],
        },
        "notifications": {
            **notifications,
            "openclaw_path": str(openclaw_path),
            "discord_target_env": discord_target_env,
            "signal_target_env": signal_target_env,
        },
    }

    result["repository"] = core_profile["repository"]
    result["review_policy"] = core_profile.get("review_policy", {})
    result["clawsweeper"] = core_profile["clawsweeper"]
    result["openclaw"] = core_profile["openclaw"]
    if generalized:
        result.update({key: config[key] for key in ("profile_id", "enrollment", "adapter")})
        if config["enrollment"]["enabled"]:
            # Connector identity is deployment-only. Standalone source
            # readiness keeps the proposed hostname isolated and does not
            # invent or require a tunnel ID.
            if not tunnel["tunnel_name"]:
                raise UserlandError("enabled profile requires an isolated tunnel name")
            if ingress["public_hostname"].endswith(".invalid"):
                raise UserlandError("enabled profile requires an isolated public hostname")
        profiles.validate_isolation([result])
        if _check_peers:
            peers = [load_config(peer, home=resolved_home, source_root=resolved_source, _check_peers=False)
                     for peer in sorted((resolved_source / "contracts/review-conductor").glob("*-userland.json"))
                     if peer.resolve() != path.resolve()]
            profiles.validate_isolation(peers + [result])
    return result


def read_inherited_value(env_name: str, label: str, *, maximum: int = 1024 * 1024) -> str:
    raw_fd = os.environ.get(env_name)
    if raw_fd is None or re.fullmatch(r"[3-9][0-9]*", raw_fd) is None:
        raise UserlandError(f"{label} inherited descriptor is unavailable")
    fd = int(raw_fd)
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = os.read(fd, min(65536, maximum + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if total > maximum:
            raise UserlandError(f"{label} exceeds its bounded size")
    try:
        value = b"".join(chunks).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise UserlandError(f"{label} is not UTF-8") from exc
    if not value:
        raise UserlandError(f"{label} is empty")
    return value


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    old_umask = os.umask(0o077)
    try:
        temporary.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        os.replace(temporary, path)
    finally:
        os.umask(old_umask)
        with contextlib.suppress(OSError):
            temporary.unlink()


def copy_proof(source: Path, destination: Path) -> tuple[Path, str]:
    metadata = source.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_size > 1024 * 1024
    ):
        raise UserlandError("rail proof has an invalid bounded file shape")
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    old_umask = os.umask(0o077)
    try:
        with source.open("rb") as reader, temporary.open("xb") as writer:
            shutil.copyfileobj(reader, writer, length=65536)
        os.replace(temporary, destination)
    finally:
        os.umask(old_umask)
        with contextlib.suppress(OSError):
            temporary.unlink()
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    return destination, digest


def parse_json_receipt(output: str, label: str) -> dict[str, Any]:
    try:
        value = json.loads(output)
    except json.JSONDecodeError:
        value = None
    if isinstance(value, dict):
        return value
    for line in reversed(output.splitlines()):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise UserlandError(f"{label} did not emit a JSON receipt")


def active_openclaw_actions(config: dict[str, Any]) -> list[sqlite3.Row]:
    connection = core.open_database(Path(config["paths"]["state_root"]), config["github_app"]["repository"])
    try:
        return connection.execute(
            """
            SELECT actions.* FROM actions
            JOIN heads
              ON heads.repository = actions.repository
             AND heads.pr_number = actions.pr_number
             AND heads.base_sha = actions.base_sha
             AND heads.head_sha = actions.head_sha
             AND heads.review_epoch = actions.review_epoch
             AND heads.is_current = 1
            WHERE actions.kind = 'openclaw.enqueue'
              AND actions.status = 'dispatched'
              AND heads.state IN ('openclaw_queued', 'openclaw_running')
            ORDER BY actions.created_at, actions.action_id
            """
        ).fetchall()
    finally:
        connection.close()


CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


@contextlib.contextmanager
def open_openclaw_fetch(source_root: Path, proof_path: str):
    """Anchor the supported transport layout; never follow receipt-selected links.

    Fetch IDs are generated by the lane, not request IDs. The caller must bind
    REQUEST_STATUS from this same directory to its action before copying bytes.
    The configured source root is trusted; descendants from it are not.
    """
    candidate = Path(proof_path)
    if ".." in candidate.parts:
        raise UserlandError("Spark proof path contains traversal")
    if candidate.is_absolute():
        try:
            candidate = candidate.relative_to(source_root)
        except ValueError as exc:
            raise UserlandError("Spark proof path escapes the source root") from exc
    parts = candidate.parts
    if (len(parts) != 4 or parts[:2] != ("runs", "spark-openclaw-autoreview-runs")
            or parts[3] != "PROOF.md"
            or not re.fullmatch(r"spark-openclaw-autoreview-[0-9]{8}T[0-9]{6}Z-[0-9]+(?:-[0-9]+)?", parts[2])):
        raise UserlandError("Spark proof path is not a supported fetched run")
    with contextlib.ExitStack() as stack:
        # Resolve only the trusted configured root (e.g. macOS /var -> /private/var).
        fd = os.open(source_root.resolve(strict=True), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        stack.callback(os.close, fd)
        try:
            for component in parts[:-1]:
                fd = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                stack.callback(os.close, fd)
        except OSError as exc:
            raise UserlandError("Spark fetch ancestors must be real directories") from exc
        yield fd


def read_openclaw_file(source: Path, limit: int, *, dir_fd: int | None = None) -> bytes:
    """Open once; type, bound and bytes all refer to the same non-symlink inode."""
    try:
        fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dir_fd)
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise UserlandError("Spark fetched file must be a regular non-symlink file") from exc
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode):
            raise UserlandError("Spark fetched file must be a regular non-symlink file")
        # Read one extra byte to detect growth past the bound without trusting stat size.
        with os.fdopen(fd, "rb", closefd=False) as stream:
            return stream.read(limit + 1)
    finally:
        os.close(fd)


def write_openclaw_bytes(destination: Path, raw: bytes) -> None:
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    old_umask = os.umask(0o077)
    try:
        with temporary.open("xb") as stream:
            stream.write(raw)
        os.replace(temporary, destination)
    finally:
        os.umask(old_umask)
        with contextlib.suppress(OSError):
            temporary.unlink()


def preserve_openclaw_report(source: Path, destination: Path, *, dir_fd: int | None = None) -> dict[str, Any]:
    """Copy only bounded native output from the already-qualified fetch directory."""
    try:
        raw = read_openclaw_file(source, core.OPENCLAW_REPORT_MAX_BYTES, dir_fd=dir_fd)
    except FileNotFoundError:
        return {"status": "missing"}
    if len(raw) > core.OPENCLAW_REPORT_MAX_BYTES:
        return {"status": "oversized"}
    try:
        text = raw.decode("utf-8")
    except UnicodeError:
        return {"status": "invalid_text"}
    if not text.strip() or any(ord(char) < 32 and char not in "\n\r\t" for char in text):
        return {"status": "invalid_text"}
    # Use the bytes just bounded and inspected, not a second read of the source.
    write_openclaw_bytes(destination, raw)
    return {"status": "available", "ref": str(destination), "sha256": hashlib.sha256(raw).hexdigest()}


def collect_openclaw_terminals(
    config: dict[str, Any],
    *,
    dry_run: bool,
    runner: CommandRunner = subprocess.run,
) -> list[dict[str, Any]]:
    outcomes: list[dict[str, Any]] = []
    for action in active_openclaw_actions(config):
        payload = json.loads(action["payload_json"])
        request_id = payload["queue_request_id"]
        if dry_run:
            outcomes.append({"request_id": request_id, "result": "status_planned"})
            continue
        command = [
            config["spark"]["smoky_path"],
            "lane",
            "run",
            "spark-openclaw-autoreview",
            "--request-id",
            request_id,
        ]
        try:
            result = core.run_generation_bound(
                runner,
                command,
                cwd=config["source_root"],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=120,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            outcomes.append({"request_id": request_id, "result": "status_unavailable"})
            continue
        if result.returncode not in {0, 3}:
            outcomes.append({"request_id": request_id, "result": "status_unavailable"})
            continue
        try:
            receipt = parse_json_receipt(result.stdout, "Spark request status")
        except core.ContractError:
            outcomes.append({"request_id": request_id, "result": "status_unavailable"})
            continue
        if receipt.get("result") not in {"completed", "failed", "needs-human"}:
            outcomes.append({"request_id": request_id, "result": "not_terminal"})
            continue
        proof_path = receipt.get("proof_path")
        if not isinstance(proof_path, str) or not proof_path:
            raise UserlandError("Spark terminal receipt omitted its proof path")
        with open_openclaw_fetch(Path(config["source_root"]), proof_path) as run_fd:
            raw_status = read_openclaw_file(Path("REQUEST_STATUS.json"), 1024 * 1024, dir_fd=run_fd)
            if len(raw_status) > 1024 * 1024:
                raise UserlandError("Spark request status exceeds its bound")
            try:
                status = json.loads(raw_status)
            except (ValueError, UnicodeError) as exc:
                raise UserlandError("Spark request status is invalid JSON") from exc
            if not isinstance(status, dict):
                raise UserlandError("Spark request status must be an object")
            if (
                status.get("id") != request_id
                or status.get("operator_id") != payload["operator_id"]
                or status.get("commit_sha") != action["head_sha"]
                or status.get("submitted_head") != action["head_sha"]
            ):
                raise UserlandError("Spark terminal status does not match the exact action")
            if config.get("review_policy"):
                expected = {"repository": action["repository"], "base_sha": action["base_sha"], "head_sha": action["head_sha"], "pr_number": action["pr_number"], "review_epoch": action["review_epoch"], "review_scope": "comprehensive", "reviewer_actor": config["review_policy"]["reviewers"]["openclaw"], "native_max_priority": "P3", "applied_max_priority": "P3", "exact_tuple_qualified": True}
                if any(not core.same_typed_value(status.get(key), value) for key, value in expected.items()):
                    raise UserlandError("Spark proof lacks trusted comprehensive exact-tuple identity")
            finding_count = status.get("review_finding_count")
            if finding_count is None and receipt["result"] in {"failed", "needs-human"}:
                finding_count = 0
            if isinstance(finding_count, bool) or not isinstance(finding_count, int) or finding_count < 0:
                raise UserlandError("Spark terminal finding count is invalid")
            destination_root = (
                Path(config["paths"]["proof_root"])
                / "openclaw"
                / request_id
            )
            proof_raw = read_openclaw_file(Path("PROOF.md"), 1024 * 1024, dir_fd=run_fd)
            if len(proof_raw) > 1024 * 1024:
                raise UserlandError("Spark proof exceeds its bound")
            proof = destination_root / "PROOF.md"
            write_openclaw_bytes(proof, proof_raw)
            proof_digest = hashlib.sha256(proof_raw).hexdigest()
            original_report = preserve_openclaw_report(
                Path("review_output.txt"), destination_root / "review_output.txt", dir_fd=run_fd
            )
        artifact = {
            "schema": runtime.OPENCLAW_ARTIFACT_SCHEMA,
            "request_id": request_id,
            "operator_id": payload["operator_id"],
            "status": receipt["result"],
            "repository": action["repository"],
            "pr_number": action["pr_number"],
            "base_sha": action["base_sha"],
            "head_sha": action["head_sha"],
            "review_epoch": action["review_epoch"],
            "review_clean": status.get("review_clean") is True,
            "review_finding_count": finding_count,
            "reviewer_actor": config.get("review_policy", {}).get("reviewers", {}).get("openclaw", "spark-openclaw"),
            "proof_ref": str(proof),
            "proof_sha256": proof_digest,
            "original_report": original_report,
        }
        if config.get("review_policy"):
            artifact["review_scope"] = status["review_scope"]
            artifact["native_max_priority"] = status["native_max_priority"]
            artifact["applied_max_priority"] = status["applied_max_priority"]
            artifact["exact_tuple_qualified"] = status["exact_tuple_qualified"]
        artifact_path = Path(config["spark"]["terminal_inbox"]) / f"{request_id}.terminal.json"
        write_json_atomic(artifact_path, artifact)
        outcomes.append({"request_id": request_id, "result": "terminal_materialized"})
    return outcomes


def parse_frontmatter(markdown: str) -> dict[str, str]:
    match = FRONTMATTER_RE.match(markdown)
    if match is None:
        raise UserlandError("ClawSweeper report lacks exact frontmatter")
    values: dict[str, str] = {}
    for line in match.group("body").splitlines():
        if not line or line[:1].isspace() or ":" not in line:
            continue
        key, value = line.split(":", 1)
        if re.fullmatch(r"[a-z][a-z0-9_]*", key) is None:
            continue
        if key in values:
            raise UserlandError("ClawSweeper report contains ambiguous duplicate metadata")
        values[key] = value.strip().strip('"')
    return values


def bounded_zip_files(raw: bytes) -> dict[str, bytes]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as exc:
        raise UserlandError("ClawSweeper review artifact is not a valid zip") from exc
    files: dict[str, bytes] = {}
    total = 0
    for info in archive.infolist():
        path = PurePosixPath(info.filename)
        mode = info.external_attr >> 16
        if (
            info.is_dir()
            or path.is_absolute()
            or ".." in path.parts
            or stat.S_ISLNK(mode)
            or info.file_size > 512 * 1024
        ):
            raise UserlandError("ClawSweeper review artifact contains an unsafe entry")
        total += info.file_size
        if total > 1024 * 1024 or info.filename in files:
            raise UserlandError("ClawSweeper review artifact exceeds its bounded shape")
        files[info.filename] = archive.read(info)
    return files


def parse_clawsweeper_bundle(
    raw: bytes,
    *,
    workflow_run_id: int,
    action: sqlite3.Row,
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    files = bounded_zip_files(raw)
    if "manifest.json" not in files:
        raise UserlandError("ClawSweeper review artifact omitted its manifest")
    try:
        manifest = json.loads(files["manifest.json"])
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UserlandError("ClawSweeper manifest is invalid") from exc
    manifest = core.require_object(manifest, "ClawSweeper manifest")
    review_path = f"review/{action['pr_number']}.md"
    if set(files) != {"manifest.json", review_path}:
        raise UserlandError("ClawSweeper review artifact has an unexpected file set")
    workflow = core.require_object(manifest.get("workflow"), "ClawSweeper workflow")
    target = core.require_object(manifest.get("target"), "ClawSweeper target")
    review = core.require_object(manifest.get("review"), "ClawSweeper review")
    if (
        str(workflow.get("run_id")) != str(workflow_run_id)
        or workflow.get("repository") != action["repository"]
        or target.get("repo") != action["repository"]
        or target.get("item_number") != action["pr_number"]
        or review.get("artifact_present") is not True
        or review.get("live_proceeded") is not True
        or review.get("live_terminal_missing") is not False
    ):
        raise UserlandError("ClawSweeper manifest does not match the exact rail action")
    entries = manifest.get("files")
    if not isinstance(entries, list) or len(entries) != 1:
        raise UserlandError("ClawSweeper manifest file inventory is invalid")
    entry = core.require_object(entries[0], "ClawSweeper manifest file")
    report_raw = files[review_path]
    if (
        entry.get("path") != review_path
        or entry.get("bytes") != len(report_raw)
        or entry.get("sha256") != hashlib.sha256(report_raw).hexdigest()
    ):
        raise UserlandError("ClawSweeper report does not match its manifest digest")
    try:
        report = report_raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise UserlandError("ClawSweeper report is not UTF-8") from exc
    frontmatter = parse_frontmatter(report)
    if (
        frontmatter.get("repository") != action["repository"]
        or frontmatter.get("number") != str(action["pr_number"])
        or frontmatter.get("main_sha") != action["base_sha"]
        or frontmatter.get("pull_head_sha") != action["head_sha"]
        or frontmatter.get("review_status") != "complete"
    ):
        raise UserlandError("ClawSweeper report identity is stale or incomplete")
    if policy:
        if (frontmatter.get("review_epoch") != str(action["review_epoch"])
            or frontmatter.get("review_scope") != "comprehensive"
            or frontmatter.get("reviewer_actor") != policy["reviewers"]["clawsweeper"]):
            raise UserlandError("ClawSweeper report lacks trusted comprehensive epoch-bound identity")
    overall_tier = frontmatter.get("pr_rating_overall")
    proof_tier = frontmatter.get("pr_rating_proof")
    proof_status = frontmatter.get("real_behavior_proof_status")
    if overall_tier not in {"S", "A", "B", "C", "D", "F", "NA"}:
        raise UserlandError("ClawSweeper overall tier is invalid")
    if proof_tier not in {"S", "A", "B", "C", "D", "F", "NA"}:
        raise UserlandError("ClawSweeper proof tier is invalid")
    if not isinstance(proof_status, str) or not proof_status:
        raise UserlandError("ClawSweeper proof status is missing")
    finding_count = len(FINDING_RE.findall(report))
    try:
        classified = projection.classify_from_frontmatter(
            frontmatter, finding_count=finding_count
        )
    except projection.ProjectionError as exc:
        raise UserlandError(str(exc)) from exc
    ready_qualified = (
        overall_tier in READY_OVERALL_TIERS
        and proof_status in READY_PROOF_STATUSES
        and classified["content_verdict"] == "clean"
    )
    return {
        "manifest": manifest,
        "report": report,
        "report_bytes": report_raw,
        "verdict": classified["rail_result"],
        "finding_count": finding_count,
        "overall_tier": overall_tier,
        "proof_tier": proof_tier,
        "proof_status": proof_status,
        "ready_qualified": ready_qualified,
        "content_verdict": classified["content_verdict"],
        "process_gates": classified["process_gates"],
        "merge_authorized": False,
    }


def ensure_userland_tables(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS clawsweeper_quality (
          repository TEXT NOT NULL,
          pr_number INTEGER NOT NULL,
          base_sha TEXT NOT NULL,
          head_sha TEXT NOT NULL,
          review_epoch INTEGER NOT NULL,
          workflow_run_id TEXT NOT NULL,
          overall_tier TEXT NOT NULL,
          proof_tier TEXT NOT NULL,
          proof_status TEXT NOT NULL,
          ready_qualified INTEGER NOT NULL,
          report_sha256 TEXT NOT NULL,
          content_verdict TEXT,
          process_gates_json TEXT,
          created_at TEXT NOT NULL,
          PRIMARY KEY(repository, pr_number, base_sha, head_sha, review_epoch)
        );
        CREATE TABLE IF NOT EXISTS notification_deliveries (
          event_key TEXT NOT NULL,
          channel TEXT NOT NULL,
          repository TEXT NOT NULL,
          pr_number INTEGER NOT NULL,
          base_sha TEXT NOT NULL,
          head_sha TEXT NOT NULL,
          review_epoch INTEGER NOT NULL,
          state TEXT NOT NULL,
          payload_json TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'pending',
          attempts INTEGER NOT NULL DEFAULT 0,
          last_error TEXT,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          PRIMARY KEY(event_key, channel)
        );
        CREATE TABLE IF NOT EXISTS notification_reconciliations (
          reconciliation_id TEXT PRIMARY KEY,
          event_key TEXT NOT NULL,
          channel TEXT NOT NULL,
          disposition TEXT NOT NULL,
          confirmation TEXT NOT NULL,
          prior_status TEXT NOT NULL,
          prior_attempts INTEGER NOT NULL,
          created_at TEXT NOT NULL,
          FOREIGN KEY(event_key, channel)
            REFERENCES notification_deliveries(event_key, channel)
        );
        """
    )
    quality_columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(clawsweeper_quality)").fetchall()
    }
    if "content_verdict" not in quality_columns:
        connection.execute("ALTER TABLE clawsweeper_quality ADD COLUMN content_verdict TEXT")
    if "process_gates_json" not in quality_columns:
        connection.execute("ALTER TABLE clawsweeper_quality ADD COLUMN process_gates_json TEXT")


def queue_operator_alert(config: dict[str, Any], alert_id: str, message: str) -> None:
    event_key = hashlib.sha256(f"operator-alert|{alert_id}".encode()).hexdigest()
    payload = {
        "schema": NOTIFICATION_SCHEMA,
        "event_key": event_key,
        "repository": config["github_app"]["repository"],
        "pr_number": 0,
        "base_sha": "0" * 40,
        "head_sha": "0" * 40,
        "review_epoch": 0,
        "state": "rail_failure_unbound",
        "repair_cycle": 0,
        "message": message,
        "merge_authorized": False,
    }
    connection = core.open_database(Path(config["paths"]["state_root"]), config["github_app"]["repository"])
    try:
        ensure_userland_tables(connection)
        for channel in ("openclaw_context", "discord", "signal"):
            connection.execute(
                """
                INSERT OR IGNORE INTO notification_deliveries(
                  event_key, channel, repository, pr_number, base_sha, head_sha,
                  review_epoch, state, payload_json, created_at, updated_at
                ) VALUES (?, ?, ?, 0, ?, ?, 0, 'rail_failure_unbound', ?, ?, ?)
                """,
                (
                    event_key,
                    channel,
                    config["github_app"]["repository"],
                    "0" * 40,
                    "0" * 40,
                    core.canonical_json(payload),
                    core.utc_now(),
                    core.utc_now(),
                ),
            )
        connection.commit()
    finally:
        connection.close()


def pending_clawsweeper_runs(config: dict[str, Any]) -> list[sqlite3.Row]:
    connection = core.open_database(Path(config["paths"]["state_root"]), config["github_app"]["repository"])
    try:
        return connection.execute(
            """
            SELECT * FROM rail_workflow_runs
            WHERE rail = 'clawsweeper' AND status = 'terminal_pending_verdict_bridge'
            ORDER BY source_created_at, workflow_run_id
            """
        ).fetchall()
    finally:
        connection.close()


def clawsweeper_bundle_identity(raw: bytes) -> dict[str, Any]:
    files = bounded_zip_files(raw)
    if "manifest.json" not in files:
        raise UserlandError("ClawSweeper review artifact omitted its manifest")
    try:
        manifest = core.require_object(
            json.loads(files["manifest.json"]), "ClawSweeper manifest"
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UserlandError("ClawSweeper manifest is invalid") from exc
    target = core.require_object(manifest.get("target"), "ClawSweeper target")
    repository = target.get("repo")
    pr_number = target.get("item_number")
    if not isinstance(repository, str) or isinstance(pr_number, bool) or not isinstance(pr_number, int):
        raise UserlandError("ClawSweeper target identity is invalid")
    review_path = f"review/{pr_number}.md"
    if review_path not in files:
        raise UserlandError("ClawSweeper artifact omitted its exact review report")
    try:
        frontmatter = parse_frontmatter(files[review_path].decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise UserlandError("ClawSweeper report is not UTF-8") from exc
    base_sha = core.require_sha(frontmatter.get("main_sha"), "ClawSweeper report base")
    head_sha = core.require_sha(frontmatter.get("pull_head_sha"), "ClawSweeper report head")
    return {
        "repository": repository,
        "pr_number": pr_number,
        "base_sha": base_sha,
        "head_sha": head_sha,
    }


def clawsweeper_action(config: dict[str, Any], identity: dict[str, Any]) -> sqlite3.Row:
    connection = core.open_database(Path(config["paths"]["state_root"]), config["github_app"]["repository"])
    try:
        row = connection.execute(
            """
            SELECT actions.* FROM actions
            JOIN heads
              ON heads.repository = actions.repository
             AND heads.pr_number = actions.pr_number
             AND heads.base_sha = actions.base_sha
             AND heads.head_sha = actions.head_sha
             AND heads.review_epoch = actions.review_epoch
             AND heads.is_current = 1
            WHERE actions.kind = 'clawsweeper.dispatch'
              AND actions.status = 'dispatched'
              AND heads.state IN ('clawsweeper_queued', 'clawsweeper_running')
              AND actions.repository = ? AND actions.pr_number = ?
              AND actions.base_sha = ? AND actions.head_sha = ?
            """,
            (
                identity["repository"], identity["pr_number"],
                identity["base_sha"], identity["head_sha"],
            ),
        ).fetchall()
        if len(row) != 1:
            raise UserlandError("ClawSweeper bundle does not match one current dispatched action")
        return row[0]
    finally:
        connection.close()


def materialize_failed_clawsweeper_terminal(
    config: dict[str, Any],
    client: runtime.GitHubAppClient | Any,
    rail_run: sqlite3.Row,
    candidate: dict[str, Any],
) -> dict[str, Any]:
    run_id = int(rail_run["workflow_run_id"])
    artifact_id = core.require_positive_int(
        candidate.get("id"), "ClawSweeper artifact id"
    )
    raw_bundle = client.download_artifact(artifact_id)
    identity = clawsweeper_bundle_identity(raw_bundle)
    action = clawsweeper_action(config, identity)
    if config.get("review_policy"):
        parse_clawsweeper_bundle(raw_bundle, workflow_run_id=run_id, action=action, policy=config["review_policy"])
    files = bounded_zip_files(raw_bundle)
    report_bytes = files[f"review/{action['pr_number']}.md"]
    destination_root = (
        Path(config["paths"]["proof_root"])
        / "clawsweeper"
        / str(run_id)
    )
    destination_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    report_path = destination_root / f"{action['pr_number']}.md"
    report_path.write_bytes(report_bytes)
    os.chmod(report_path, 0o600)
    artifact_path = (
        Path(config["clawsweeper_bridge"]["terminal_inbox"])
        / f"{run_id}.terminal.json"
    )
    write_json_atomic(
        artifact_path,
        {
            "schema": runtime.CLAWSWEEPER_ARTIFACT_SCHEMA,
            **({"review_scope": "comprehensive"} if config.get("review_policy") else {}),
            "workflow_run_id": run_id,
            "repository": action["repository"],
            "pr_number": action["pr_number"],
            "base_sha": action["base_sha"],
            "head_sha": action["head_sha"],
            "review_epoch": action["review_epoch"],
            "verdict": "failed",
            "finding_count": 0,
            "reviewer_actor": config.get("review_policy", {}).get("reviewers", {}).get("clawsweeper", "clawsweeper"),
            "proof_ref": str(report_path),
            "proof_sha256": hashlib.sha256(report_bytes).hexdigest(),
        },
    )
    return {
        "workflow_run_id": run_id,
        "result": "terminal_failure_materialized",
        "conclusion": rail_run["conclusion"],
        "pr_number": action["pr_number"],
        "head_sha": action["head_sha"],
    }



def fail_bound_clawsweeper_execution(
    connection: sqlite3.Connection, rail_run: sqlite3.Row,
) -> dict[str, Any] | None:
    """Fail only an already-started exact execution; never infer a PR from time.

    This records transport failure, not a reviewer terminal or content verdict.
    The caller holds the write transaction used to retire the workflow receipt.
    """
    matches = connection.execute(
        """
        SELECT heads.* FROM heads JOIN actions
          ON actions.repository = heads.repository
         AND actions.pr_number = heads.pr_number
         AND actions.base_sha = heads.base_sha AND actions.head_sha = heads.head_sha
         AND actions.review_epoch = heads.review_epoch
        WHERE heads.is_current = 1 AND heads.rail = 'clawsweeper'
          AND heads.state = 'clawsweeper_running' AND heads.review_request_id = ?
          AND actions.kind = 'clawsweeper.dispatch' AND actions.status = 'dispatched'
        """,
        (rail_run["workflow_run_id"],),
    ).fetchall()
    if len(matches) != 1:
        return None
    row = matches[0]
    identity = {key: row[key] for key in ("repository", "pr_number", "base_sha", "head_sha")}
    reason = f"ClawSweeper execution {rail_run['conclusion']}; no qualified verdict bundle; operator recovery required"
    core.update_exact_head(connection, identity, state="clawsweeper_failed", blocker=reason)
    connection.execute(
        """
        UPDATE rail_workflow_runs SET bound_repository = ?, bound_pr_number = ?,
          bound_base_sha = ?, bound_head_sha = ?, bound_review_epoch = ?
        WHERE rail = 'clawsweeper' AND workflow_run_id = ?
          AND status = 'terminal_pending_verdict_bridge'
        """,
        (*identity.values(), row["review_epoch"], rail_run["workflow_run_id"]),
    )
    core.insert_event(
        connection, event_id=f"clawsweeper-execution-failed:{rail_run['workflow_run_id']}",
        kind="clawsweeper.execution_failed", stale=False,
        payload={"workflow_run_id": rail_run["workflow_run_id"],
                 "conclusion": rail_run["conclusion"], "review_epoch": row["review_epoch"],
                 "verdict_available": False}, **identity,
    )
    return {**identity, "review_epoch": row["review_epoch"]}


def collect_clawsweeper_terminals(
    config: dict[str, Any],
    client: runtime.GitHubAppClient | Any | None,
    *,
    dry_run: bool,
) -> list[dict[str, Any]]:
    outcomes: list[dict[str, Any]] = []
    for rail_run in pending_clawsweeper_runs(config):
        run_id = int(rail_run["workflow_run_id"])
        if dry_run:
            outcomes.append({"workflow_run_id": run_id, "result": "artifact_planned"})
            continue
        if client is None:
            raise UserlandError("GitHub client is required to collect ClawSweeper proof")
        expected_prefix = f"{config.get('adapter', {}).get('artifact_prefix', 'dinkuskit-native-review')}-{run_id}-"
        candidates = [
            item
            for item in client.list_run_artifacts(run_id)
            if isinstance(item.get("name"), str)
            and item["name"].startswith(expected_prefix)
            and item.get("expired") is False
        ]
        if rail_run["conclusion"] != "success":
            if len(candidates) == 1:
                try:
                    outcomes.append(
                        materialize_failed_clawsweeper_terminal(
                            config, client, rail_run, candidates[0]
                        )
                    )
                    continue
                except UserlandError:
                    pass
            queue_operator_alert(
                config,
                f"clawsweeper-workflow-{run_id}-{rail_run['conclusion']}",
                (
                    f"Review Conductor needs Bobby: ClawSweeper workflow run {run_id} "
                    f"concluded {rail_run['conclusion']} before an exact PR verdict bundle was available. "
                    "No PR was marked ready. No merge was attempted. "
                    f"https://github.com/{config['github_app']['repository']}/actions/runs/{run_id}"
                ),
            )
            connection = core.open_database(Path(config["paths"]["state_root"]), config["github_app"]["repository"])
            try:
                connection.execute("BEGIN IMMEDIATE")
                bound_failure = fail_bound_clawsweeper_execution(connection, rail_run)
                connection.execute(
                    """
                    UPDATE rail_workflow_runs SET status = 'terminal_attention_required'
                    WHERE rail = 'clawsweeper' AND workflow_run_id = ?
                      AND status = 'terminal_pending_verdict_bridge'
                    """,
                    (str(run_id),),
                )
                connection.commit()
            finally:
                connection.close()
            outcomes.append(
                {
                    "workflow_run_id": run_id,
                    "result": "bound_execution_failed" if bound_failure else "rail_failure_alerted",
                    "conclusion": rail_run["conclusion"],
                    **({"binding": bound_failure} if bound_failure else {}),
                }
            )
            continue
        if len(candidates) != 1:
            raise UserlandError("ClawSweeper run does not expose one exact review artifact")
        artifact_id = core.require_positive_int(
            candidates[0].get("id"), "ClawSweeper artifact id"
        )
        raw_bundle = client.download_artifact(artifact_id)
        action = clawsweeper_action(config, clawsweeper_bundle_identity(raw_bundle))
        parsed = parse_clawsweeper_bundle(
            raw_bundle,
            workflow_run_id=run_id,
            action=action,
            policy=config.get("review_policy"),
        )
        destination_root = (
            Path(config["paths"]["proof_root"])
            / "clawsweeper"
            / str(run_id)
        )
        destination_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        report_path = destination_root / f"{action['pr_number']}.md"
        report_path.write_bytes(parsed["report_bytes"])
        os.chmod(report_path, 0o600)
        report_digest = hashlib.sha256(parsed["report_bytes"]).hexdigest()
        write_json_atomic(destination_root / "manifest.json", parsed["manifest"])
        bridge_artifact = {
            "schema": runtime.CLAWSWEEPER_ARTIFACT_SCHEMA,
            **({"review_scope": "comprehensive"} if config.get("review_policy") else {}),
            "workflow_run_id": run_id,
            "repository": action["repository"],
            "pr_number": action["pr_number"],
            "base_sha": action["base_sha"],
            "head_sha": action["head_sha"],
            "review_epoch": action["review_epoch"],
            "verdict": parsed["verdict"],
            "finding_count": parsed["finding_count"],
            "reviewer_actor": config.get("review_policy", {}).get("reviewers", {}).get("clawsweeper", "clawsweeper"),
            "proof_ref": str(report_path),
            "proof_sha256": report_digest,
        }
        artifact_path = (
            Path(config["clawsweeper_bridge"]["terminal_inbox"])
            / f"{run_id}.terminal.json"
        )
        write_json_atomic(artifact_path, bridge_artifact)
        connection = core.open_database(Path(config["paths"]["state_root"]), config["github_app"]["repository"])
        try:
            ensure_userland_tables(connection)
            connection.execute(
                """
                INSERT INTO clawsweeper_quality(
                  repository, pr_number, base_sha, head_sha, review_epoch,
                  workflow_run_id, overall_tier, proof_tier, proof_status,
                  ready_qualified, report_sha256, content_verdict, process_gates_json,
                  created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(repository, pr_number, base_sha, head_sha, review_epoch)
                DO UPDATE SET workflow_run_id = excluded.workflow_run_id,
                  overall_tier = excluded.overall_tier,
                  proof_tier = excluded.proof_tier,
                  proof_status = excluded.proof_status,
                  ready_qualified = excluded.ready_qualified,
                  report_sha256 = excluded.report_sha256,
                  content_verdict = excluded.content_verdict,
                  process_gates_json = excluded.process_gates_json
                """,
                (
                    action["repository"], action["pr_number"], action["base_sha"],
                    action["head_sha"], action["review_epoch"], str(run_id),
                    parsed["overall_tier"], parsed["proof_tier"],
                    parsed["proof_status"], int(parsed["ready_qualified"]),
                    report_digest, parsed["content_verdict"],
                    json.dumps(parsed["process_gates"], separators=(",", ":")),
                    core.utc_now(),
                ),
            )
            connection.commit()
        finally:
            connection.close()
        outcomes.append(
            {
                "workflow_run_id": run_id,
                "result": "terminal_materialized",
                "verdict": parsed["verdict"],
                "ready_qualified": parsed["ready_qualified"],
            }
        )
    return outcomes


CONCISE_BLOCKED_REASONS = {
    "unknown_state_fail_closed": "unknown state",
    "unknown_rail_result_fail_closed": "unknown rail result",
    "inconsistent_state_rail_result_fail_closed": "inconsistent state rail result",
    "unknown_outcome_fail_closed": "unknown orchestration state",
    "ambiguous_or_broken_enrollment": "ambiguous or broken enrollment",
    "human_action_required": "human gate",
    "ci_failed": "CI failed",
    "openclaw_failed": "OpenClaw failed",
    "clawsweeper_failed": "ClawSweeper failed",
    "waiting_human": "human gate",
}


def notification_message(
    row: sqlite3.Row,
    quality: sqlite3.Row | None,
    decision: dict[str, Any] | None = None,
) -> str:
    repo_pr = f"{row['repository']}#{row['pr_number']}"
    url = f"https://github.com/{row['repository']}/pull/{row['pr_number']}"
    eligibility = None if decision is None else decision["notification"]["eligibility"]
    if eligibility == "merge_ready" or (
        eligibility is None and row["state"] == "ready_for_human_merge"
    ):
        return f"{repo_pr} ready to merge {url}"
    if decision is not None and decision.get("route") == "fail_closed":
        reason = CONCISE_BLOCKED_REASONS.get(
            decision["reason"], decision["reason"].replace("_", " ")
        )
    else:
        blocker = str(row["blocker"] or "").strip()
        if blocker:
            reason = blocker
        elif decision is not None:
            reason = CONCISE_BLOCKED_REASONS.get(
                decision["reason"], decision["reason"].replace("_", " ")
            )
        else:
            reason = CONCISE_BLOCKED_REASONS.get(row["state"], "operator attention required")
    return f"{repo_pr} blocked — {reason} {url}"


def checkout_fingerprint(path: Path) -> tuple[int, int, int, int, int]:
    metadata = path.lstat()
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise UserlandError("same-user Blocks checkout must be a real directory")
    if metadata.st_uid != os.getuid() or metadata.st_mode & 0o022:
        raise UserlandError("same-user Blocks checkout owner or mode is unsafe")
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_uid,
        metadata.st_gid,
        stat.S_IMODE(metadata.st_mode),
    )


def checkout_git_command(checkout: Path, *arguments: str) -> list[str]:
    return [
        str(runtime.FIXED_GIT),
        "--no-pager",
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "maintenance.auto=false",
        "-c",
        "gc.auto=0",
        "-C",
        str(checkout),
        *arguments,
    ]


def checkout_git_environment() -> dict[str, str]:
    return {
        "HOME": os.environ.get("HOME", "/var/empty"),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_NO_LAZY_FETCH": "1",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_PAGER": "cat",
        "PAGER": "cat",
        "LC_ALL": "C",
        "LANG": "C",
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
    }


class HydrationFailure(UserlandError):
    """Closed failure classification; never carries Git or credential output."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(f"exact PR hydration failed ({reason})")


HYDRATION_PACK_BYTES = 256 * 1024 * 1024
HYDRATION_EXPANDED_BYTES = 512 * 1024 * 1024
HYDRATION_OBJECT_LIMIT = 100_000


def hydration_git_command(directory: Path, *arguments: str) -> list[str]:
    # Exec wrapper applies inherited OS limits without thread-unsafe preexec_fn.
    return [sys.executable, "-I", str(TOOLS / "git_hydration_exec.py"),
            *checkout_git_command(directory, *arguments)]


def fetch_hydration_pack(
    checkout: Path, action: Any, client: Any, *, runner: CommandRunner,
) -> None:
    try:
        _fetch_hydration_pack(checkout, action, client, runner=runner)
    except OSError:
        raise HydrationFailure("local_resource_unavailable") from None
    except runtime.GitHubTransientError:
        raise
    except runtime.GitHubApiError:
        raise HydrationFailure("service_auth_unavailable") from None


def _fetch_hydration_pack(
    checkout: Path, action: Any, client: Any, *, runner: CommandRunner,
) -> None:
    """Fetch in an independent bare store; import only a validated object pack.

    The checkout's config, helpers, hooks, refs and worktree never participate
    in the authenticated transport. Temporary files contain Git objects only.
    """
    authority = runtime.tuple_authority(action)
    credentials = getattr(client, "hydration_credentials", None)
    if not callable(credentials):
        raise HydrationFailure("service_auth_unavailable")
    repository = authority["repository"]
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise HydrationFailure("repository_mismatch")
    head, base = authority["head_sha"], authority["base_sha"]
    checkout_identity = checkout_fingerprint(checkout)
    with tempfile.TemporaryDirectory(prefix="conductor-hydration-") as temporary:
        staging = Path(temporary) / "objects.git"
        staging.mkdir(mode=0o700)

        def run(directory: Path, *args: str, input: Any = None,
                output: Any = subprocess.PIPE, text: bool = True,
                fd: int | None = None,
                operation: str | None = None) -> subprocess.CompletedProcess:
            def bound_runner(command: list[str], **kwargs: Any):
                # Preserve the owned service generation, plus only our pipe.
                if fd is not None:
                    kwargs["pass_fds"] = (*kwargs["pass_fds"], fd)
                if operation is not None:
                    runtime.assert_authority(client, operation, authority)
                return runner(command, **kwargs)
            try:
                return core.run_generation_bound(
                    bound_runner, hydration_git_command(directory, *args),
                    cwd="/", env={**checkout_git_environment(), "HOME": str(staging)}, input=input,
                    stdout=output, stderr=subprocess.DEVNULL, text=text,
                    timeout=180, check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                raise HydrationFailure("transport_unavailable") from None

        if run(staging, "init", "--bare", "--template=/dev/null").returncode:
            raise HydrationFailure("staging_unavailable")
        runtime.assert_authority(client, "checkout-hydration:fetch", authority)
        with credentials(authority) as (helper, descriptor):
            result = run(
                staging, "-c", "credential.helper=", "-c", f"credential.helper={helper}",
                "-c", "credential.useHttpPath=true", "-c", "credential.interactive=false",
                "-c", "http.followRedirects=false", "-c", "http.sslVerify=true",
                "-c", "http.proxy=", "-c", "http.extraHeader=",
                "-c", "fetch.unpackLimit=0", "-c", "pack.threads=1",
                "-c", "protocol.allow=never", "-c", "protocol.https.allow=always",
                "fetch", "--no-tags", "--no-recurse-submodules", "--no-write-fetch-head",
                f"https://github.com/{repository}.git",
                f"refs/pull/{authority['pr_number']}/head:refs/heads/hydration",
                base, fd=descriptor, output=subprocess.DEVNULL,
                operation="checkout-hydration:fetch-spawn",
            )
        if result.returncode:
            raise HydrationFailure("authenticated_fetch_failed")
        resolved = run(staging, "rev-parse", "--verify", "refs/heads/hydration^{commit}")
        ancestry = run(staging, "merge-base", "--is-ancestor", base, head)
        if resolved.returncode or resolved.stdout.strip() != head or ancestry.returncode:
            raise HydrationFailure("fetched_tuple_mismatch")
        # Forced packed fetch bounds each incoming pack as it is written via
        # RLIMIT_FSIZE. Check aggregate stored bytes and all expanded object
        # headers before repacking/import; no object contents enter Python RAM.
        stored = sum(path.stat().st_size for path in (staging / "objects").rglob("*")
                     if path.is_file())
        if stored > HYDRATION_PACK_BYTES:
            raise HydrationFailure("fetched_object_budget_exceeded")
        inventory = Path(temporary) / "object-sizes"
        with inventory.open("wb") as destination:
            sized = run(staging, "cat-file", "--batch-all-objects",
                        "--batch-check=%(objectsize)", output=destination)
        if sized.returncode:
            raise HydrationFailure("object_inventory_unavailable")
        count = expanded = 0
        with inventory.open("rb") as sizes:
            while line := sizes.readline(32):
                if not re.fullmatch(rb"[0-9]{1,20}\n", line):
                    raise HydrationFailure("object_inventory_invalid")
                count += 1
                expanded += int(line)
                if count > HYDRATION_OBJECT_LIMIT or expanded > HYDRATION_EXPANDED_BYTES:
                    raise HydrationFailure("expanded_object_budget_exceeded")
        pack = Path(temporary) / "objects.pack"
        with pack.open("wb") as destination:
            packed = run(staging, "pack-objects", "--threads=1", "--window-memory=64m", "--stdout", "--revs",
                         input=f"{head}\n{base}\n".encode(), output=destination, text=False)
        if packed.returncode or not 0 < pack.stat().st_size <= HYDRATION_PACK_BYTES:
            raise HydrationFailure("object_pack_unavailable")
        runtime.assert_authority(client, "checkout-hydration:import", authority)
        if checkout_fingerprint(checkout) != checkout_identity:
            raise HydrationFailure("checkout_identity_changed")
        # index-pack does not execute an upload-pack or perform network transport;
        # target local credential/URL configuration therefore cannot redirect it.
        with pack.open("rb") as source:
            try:
                def import_runner(command: list[str], **kwargs: Any):
                    runtime.assert_authority(client, "checkout-hydration:import-spawn", authority)
                    return runner(command, **kwargs)
                imported = core.run_generation_bound(
                    import_runner, hydration_git_command(checkout, "index-pack", "--threads=1",
                                                        "--stdin", "--strict",
                                                        f"--max-input-size={HYDRATION_PACK_BYTES}"),
                    cwd="/", env=checkout_git_environment(), stdin=source,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    timeout=180, check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                raise HydrationFailure("object_import_unavailable") from None
        if imported.returncode:
            raise HydrationFailure("object_import_failed")
        runtime.assert_authority(client, "checkout-hydration:imported", authority)


def hydrate_exact_pr_head(
    config: dict[str, Any],
    action: sqlite3.Row,
    *,
    runner: CommandRunner = subprocess.run,
    authority_client: Any | None = None,
) -> dict[str, Any]:
    checkout = Path(config["paths"]["blocks_checkout"])
    try:
        resolved = checkout.resolve(strict=True)
        initial_identity = checkout_fingerprint(checkout)
    except OSError as exc:
        raise UserlandError("same-user Blocks checkout is unavailable") from exc
    if resolved != checkout:
        raise UserlandError("same-user Blocks checkout path may not use aliases")

    def git(*arguments: str, timeout: int = 20) -> subprocess.CompletedProcess[str]:
        try:
            result = core.run_generation_bound(
                runner,
                checkout_git_command(checkout, *arguments),
                cwd="/",
                env=checkout_git_environment(),
                stdin=subprocess.DEVNULL,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise UserlandError("exact PR head hydration command is unavailable") from exc
        try:
            current_identity = checkout_fingerprint(checkout)
        except OSError as exc:
            raise UserlandError("same-user Blocks checkout disappeared during hydration") from exc
        if current_identity != initial_identity:
            raise UserlandError("same-user Blocks checkout identity changed during hydration")
        return result

    top = git("rev-parse", "--show-toplevel")
    origin = git("remote", "get-url", "origin")
    status = git("status", "--porcelain")
    current_head = git("rev-parse", "--verify", "HEAD^{commit}")
    refs_before = git("for-each-ref", "--format=%(refname)%09%(objectname)")
    if any(item.returncode != 0 for item in (top, origin, status, current_head, refs_before)):
        raise UserlandError("same-user Blocks checkout failed exact hydration preflight")
    try:
        top_path = Path(top.stdout.strip()).resolve(strict=True)
    except OSError as exc:
        raise UserlandError("same-user Blocks checkout root is unavailable") from exc
    if top_path != checkout:
        raise UserlandError("same-user Blocks checkout is not its git worktree root")
    if origin.stdout.strip() != f"https://github.com/{config['github_app']['repository']}.git":
        raise UserlandError("same-user Blocks checkout origin differs from dinkuskit/blocks")
    if status.stdout.strip():
        raise UserlandError("same-user Blocks checkout is dirty")
    original_head = core.require_sha(current_head.stdout.strip(), "Blocks checkout HEAD")
    head_sha = core.require_sha(action["head_sha"], "OpenClaw action head")
    base_sha = core.require_sha(action["base_sha"], "OpenClaw action base")
    authority = runtime.tuple_authority(action)

    if authority["repository"] != config["github_app"]["repository"]:
        raise HydrationFailure("repository_mismatch")
    available = git("cat-file", "-e", f"{head_sha}^{{commit}}")
    base_available = git("cat-file", "-e", f"{base_sha}^{{commit}}")
    fetched = False
    if available.returncode != 0 or base_available.returncode != 0:
        runtime.assert_authority(
            authority_client, f"checkout-hydration:{action['action_id']}:fetch", authority,
        )
        fetch_hydration_pack(checkout, action, authority_client, runner=runner)
        fetched = True

    resolved_head = git("rev-parse", "--verify", f"{head_sha}^{{commit}}")
    ancestry = git("merge-base", "--is-ancestor", base_sha, head_sha)
    final_status = git("status", "--porcelain")
    final_head = git("rev-parse", "--verify", "HEAD^{commit}")
    refs_after = git("for-each-ref", "--format=%(refname)%09%(objectname)")
    if (
        resolved_head.returncode != 0
        or resolved_head.stdout.strip() != head_sha
        or ancestry.returncode != 0
        or final_status.returncode != 0
        or final_status.stdout.strip()
        or final_head.returncode != 0
        or final_head.stdout.strip() != original_head
        or refs_after.returncode != 0
        or refs_after.stdout != refs_before.stdout
    ):
        raise UserlandError("exact PR head hydration changed checkout state or resolved the wrong tuple")
    return {
        "action_id": action["action_id"],
        "pr_number": action["pr_number"],
        "head_sha": head_sha,
        "result": "fetched" if fetched else "already_present",
        "refs_changed": False,
        "worktree_changed": False,
    }


def hydrate_pending_openclaw_heads(
    config: dict[str, Any], *, dry_run: bool, authority_client: Any | None = None
) -> list[dict[str, Any]]:
    connection = core.open_database(Path(config["paths"]["state_root"]), config["github_app"]["repository"])
    try:
        rows = connection.execute(
            """
            SELECT actions.* FROM actions
            JOIN heads
              ON heads.repository = actions.repository
             AND heads.pr_number = actions.pr_number
             AND heads.base_sha = actions.base_sha
             AND heads.head_sha = actions.head_sha
             AND heads.review_epoch = actions.review_epoch
             AND heads.is_current = 1
            WHERE actions.kind = 'openclaw.enqueue'
              AND actions.status = 'pending'
              AND heads.state = 'openclaw_queued'
            ORDER BY actions.created_at, actions.action_id
            """
        ).fetchall()
    finally:
        connection.close()
    if dry_run:
        return [
            {
                "action_id": row["action_id"],
                "pr_number": row["pr_number"],
                "head_sha": row["head_sha"],
                "result": "planned",
            }
            for row in rows
        ]
    outcomes: list[dict[str, Any]] = []
    for row in rows:
        authority = runtime.tuple_authority(row)
        # Admission revocation is not a checkout failure. Keep the fence outside
        # the adapter-error handler so it stops the tick instead of marking the
        # action failed and continuing under revoked authority.
        runtime.assert_authority(
            authority_client,
            f"checkout-hydration:{row['action_id']}",
            authority,
        )
        try:
            outcomes.append(
                hydrate_exact_pr_head(
                    config, row, authority_client=authority_client
                )
            )
        except (core.AuthorityDenied, runtime.GitHubTransientError):
            # Leave the action pending. The existing service worker catches the
            # contract error and waits for its next configured tick.
            raise
        except core.ContractError as exc:
            reason = exc.reason if isinstance(exc, HydrationFailure) else "checkout_validation_failed"
            connection = core.open_database(Path(config["paths"]["state_root"]), config["github_app"]["repository"])
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """
                    UPDATE actions SET status = 'failed', attempts = attempts + 1,
                      last_error = ?, updated_at = ?
                    WHERE action_id = ? AND status = 'pending'
                    """,
                    (f"exact PR head hydration failed ({reason})", core.utc_now(), row["action_id"]),
                )
                connection.commit()
            finally:
                connection.close()
            outcomes.append(
                {
                    "action_id": row["action_id"],
                    "pr_number": row["pr_number"],
                    "head_sha": row["head_sha"],
                    "result": "failed",
                }
            )
    return outcomes


def retry_failed_openclaw(
    config: dict[str, Any],
    pr_number: int,
    *,
    apply: bool,
    authority_guard: Callable[[sqlite3.Connection], None] | None = None,
) -> dict[str, Any]:
    connection = core.open_database(Path(config["paths"]["state_root"]), config["github_app"]["repository"])
    try:
        connection.execute("BEGIN IMMEDIATE")
        if authority_guard is not None:
            authority_guard(connection)
        head = core.current_head(connection, config["github_app"]["repository"], pr_number)
        if head is None or head["state"] != "openclaw_failed":
            raise UserlandError("PR does not have a current failed OpenClaw adapter action")
        action = core.tuple_action(
            connection,
            {
                "repository": head["repository"],
                "pr_number": head["pr_number"],
                "base_sha": head["base_sha"],
                "head_sha": head["head_sha"],
            },
            "openclaw.enqueue",
            int(head["review_epoch"]),
        )
        if action is None or action["status"] != "failed":
            raise UserlandError("current OpenClaw action is not in the failed state")
        result = {
            "schema": "smoky.review-conductor.userland-retry.v1",
            "result": "retried" if apply else "planned",
            "pr_number": pr_number,
            "action_id": action["action_id"],
            "prior_attempts": action["attempts"],
            "merge_dispatched": False,
        }
        if not apply:
            connection.rollback()
            return result
        updated = connection.execute(
            """
            UPDATE actions SET status = 'pending', claim_owner = NULL,
              claimed_at = NULL, lease_expires_at = NULL, last_error = NULL,
              updated_at = ?
            WHERE action_id = ? AND status = 'failed'
            """,
            (core.utc_now(), action["action_id"]),
        )
        if updated.rowcount != 1:
            raise UserlandError("failed OpenClaw action changed during retry")
        core.update_exact_head(
            connection,
            {
                "repository": head["repository"],
                "pr_number": head["pr_number"],
                "base_sha": head["base_sha"],
                "head_sha": head["head_sha"],
            },
            state="openclaw_queued",
            rail="openclaw",
            blocker=None,
        )
        connection.commit()
        return result
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def reconcile_uncertain_notification(
    config: dict[str, Any],
    pr_number: int,
    channel: str,
    disposition: str,
    confirmation: str,
    *,
    apply: bool,
    authority_guard: Callable[[sqlite3.Connection], None] | None = None,
) -> dict[str, Any]:
    confirmations = {
        "sent": "provider-delivery-observed",
        "retry": "provider-nondelivery-observed",
    }
    if channel not in {"openclaw_context", "discord", "signal"}:
        raise UserlandError("notification channel is unsupported")
    if disposition not in confirmations:
        raise UserlandError("notification disposition must be sent or retry")
    if confirmation != confirmations[disposition]:
        raise UserlandError("notification reconciliation confirmation is invalid")
    connection = core.open_database(Path(config["paths"]["state_root"]), config["github_app"]["repository"])
    try:
        ensure_userland_tables(connection)
        connection.execute("BEGIN IMMEDIATE")
        if authority_guard is not None:
            authority_guard(connection)
        head = core.current_head(connection, config["github_app"]["repository"], pr_number)
        if head is None:
            raise UserlandError("PR does not have a current Review Conductor head")
        rows = connection.execute(
            """
            SELECT notification_deliveries.*
            FROM notification_deliveries
            WHERE repository = ? AND pr_number = ? AND base_sha = ?
              AND head_sha = ? AND review_epoch = ? AND channel = ?
              AND status = 'uncertain'
            ORDER BY created_at, event_key
            """,
            (
                head["repository"], head["pr_number"], head["base_sha"],
                head["head_sha"], head["review_epoch"], channel,
            ),
        ).fetchall()
        if len(rows) != 1:
            raise UserlandError(
                "current PR must have exactly one uncertain notification for the channel"
            )
        row = rows[0]
        reconciliation_id = hashlib.sha256(
            (
                f"{row['event_key']}|{channel}|{row['attempts']}|"
                f"{disposition}|{confirmation}"
            ).encode()
        ).hexdigest()
        result = {
            "schema": "smoky.review-conductor.notification-reconciliation.v1",
            "result": "reconciled" if apply else "planned",
            "pr_number": pr_number,
            "channel": channel,
            "disposition": disposition,
            "prior_attempts": row["attempts"],
            "merge_dispatched": False,
        }
        if not apply:
            connection.rollback()
            return result
        connection.execute(
            """
            INSERT INTO notification_reconciliations(
              reconciliation_id, event_key, channel, disposition, confirmation,
              prior_status, prior_attempts, created_at
            ) VALUES (?, ?, ?, ?, ?, 'uncertain', ?, ?)
            """,
            (
                reconciliation_id, row["event_key"], channel, disposition,
                confirmation, row["attempts"], core.utc_now(),
            ),
        )
        next_status = "sent" if disposition == "sent" else "pending"
        updated = connection.execute(
            """
            UPDATE notification_deliveries
            SET status = ?, last_error = ?, updated_at = ?
            WHERE event_key = ? AND channel = ? AND status = 'uncertain'
            """,
            (
                next_status, f"operator-confirmed-{confirmation}", core.utc_now(),
                row["event_key"], channel,
            ),
        )
        if updated.rowcount != 1:
            raise UserlandError("uncertain notification changed during reconciliation")
        connection.commit()
        return result
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def notification_event_identity(
    row: Mapping[str, Any],
    decision: Mapping[str, Any],
    *,
    failure_attempt: int = 0,
) -> str:
    """Canonical queue identity: exact tuple plus decision route/reason/eligibility."""
    eligibility = decision["notification"]["eligibility"]
    identity = (
        f"{row['repository']}|{row['pr_number']}|{row['base_sha']}|"
        f"{row['head_sha']}|{row['review_epoch']}|{row['state']}|{row['repair_cycle']}|"
        f"{decision['route']}|{decision['reason']}|{eligibility}"
    )
    if row["state"] == "openclaw_failed":
        identity += f"|{failure_attempt}"
    return identity


def queue_notifications(
    config: dict[str, Any],
    enrollment: Mapping[str, str] | None = None,
    *,
    authoritative: bool = False,
) -> int:
    trusted_enrollment = orchestration.effective_trusted_enrollment(
        config, enrollment, authoritative=authoritative
    )
    connection = core.open_database(Path(config["paths"]["state_root"]), config["github_app"]["repository"])
    created = 0
    try:
        ensure_userland_tables(connection)
        rows = connection.execute(
            "SELECT * FROM heads WHERE is_current = 1 ORDER BY pr_number"
        ).fetchall()
        for row in rows:
            quality = connection.execute(
                """
                SELECT * FROM clawsweeper_quality
                WHERE repository = ? AND pr_number = ? AND base_sha = ?
                  AND head_sha = ? AND review_epoch = ?
                """,
                (
                    row["repository"], row["pr_number"], row["base_sha"],
                    row["head_sha"], row["review_epoch"],
                ),
            ).fetchone()
            decision = orchestration.decide_orchestration_outcome(
                orchestration.outcome_from_review_row(
                    row, quality, enrollment=trusted_enrollment
                )
            )
            eligibility = decision["notification"]["eligibility"]
            if eligibility in {"silent", "none"}:
                continue
            if eligibility not in {"merge_ready", "blocked"}:
                raise orchestration.OrchestrationError(
                    decision["reason"] or "notification eligibility is not actionable"
                )
            failure_attempt = 0
            if row["state"] == "openclaw_failed":
                failed_action = core.tuple_action(
                    connection,
                    {
                        "repository": row["repository"],
                        "pr_number": row["pr_number"],
                        "base_sha": row["base_sha"],
                        "head_sha": row["head_sha"],
                    },
                    "openclaw.enqueue",
                    int(row["review_epoch"]),
                )
                failure_attempt = int(failed_action["attempts"]) if failed_action else 0
            channels = decision["notification"]["channels"]
            event_identity = notification_event_identity(
                row, decision, failure_attempt=failure_attempt
            )
            event_key = hashlib.sha256(event_identity.encode()).hexdigest()
            payload = {
                "schema": NOTIFICATION_SCHEMA,
                "event_key": event_key,
                "repository": row["repository"],
                "pr_number": row["pr_number"],
                "base_sha": row["base_sha"],
                "head_sha": row["head_sha"],
                "review_epoch": row["review_epoch"],
                "state": row["state"],
                "repair_cycle": row["repair_cycle"],
                "message": notification_message(row, quality, decision),
                "merge_authorized": False,
                "orchestration_outcome": {
                    "schema": decision["schema"],
                    "route": decision["route"],
                    "notification": decision["notification"],
                    "reason": decision["reason"],
                },
            }
            for channel in channels:
                inserted = connection.execute(
                    """
                    INSERT OR IGNORE INTO notification_deliveries(
                      event_key, channel, repository, pr_number, base_sha, head_sha,
                      review_epoch, state, payload_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event_key, channel, row["repository"], row["pr_number"],
                        row["base_sha"], row["head_sha"], row["review_epoch"],
                        row["state"], core.canonical_json(payload), core.utc_now(),
                        core.utc_now(),
                    ),
                )
                created += inserted.rowcount
        connection.commit()
        return created
    finally:
        connection.close()


NOTIFICATION_RETIRE_REASON = (
    "no longer eligible after close, supersession, or enrollment route change"
)
CURRENT_DECISION_IDENTITY_KEYS = ("schema", "route", "reason", "notification")
CURRENT_NOTIFICATION_IDENTITY_KEYS = ("eligibility", "kind", "channels")


def current_notification_decision_identity(
    decision: Mapping[str, Any],
) -> dict[str, Any]:
    """Exact current schema/route/reason/notification identity for queue rows."""
    notification = decision["notification"]
    return {
        "schema": decision["schema"],
        "route": decision["route"],
        "reason": decision["reason"],
        "notification": {
            "eligibility": notification["eligibility"],
            "kind": notification["kind"],
            "channels": list(notification["channels"]),
        },
    }


def pending_decision_matches_current(
    prior: Any, decision: Mapping[str, Any]
) -> bool:
    """Return whether a stored decision is the exact current identity.

    Missing, partial, or legacy identities retire fail-closed. Only an exact
    current schema, route, reason, and notification may remain eligible.
    """
    expected = current_notification_decision_identity(decision)
    if not isinstance(prior, dict) or set(prior) != set(CURRENT_DECISION_IDENTITY_KEYS):
        return False
    if (
        prior.get("schema") != expected["schema"]
        or prior.get("route") != expected["route"]
        or prior.get("reason") != expected["reason"]
    ):
        return False
    notification = prior.get("notification")
    expected_notification = expected["notification"]
    if (
        not isinstance(notification, dict)
        or set(notification) != set(CURRENT_NOTIFICATION_IDENTITY_KEYS)
    ):
        return False
    return (
        notification.get("eligibility") == expected_notification["eligibility"]
        and notification.get("kind") == expected_notification["kind"]
        and notification.get("channels") == expected_notification["channels"]
    )


def pending_review_notification_still_eligible(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
    trusted_enrollment: Mapping[str, str],
) -> bool:
    """Return whether a pending review-result row may still be claimed.

    Operator alerts stay unbound. Review-result rows must still match the
    current head tuple and a currently eligible trusted decision.
    """
    if int(row["pr_number"]) == 0:
        return True
    head = connection.execute(
        """
        SELECT * FROM heads
        WHERE repository = ? AND pr_number = ? AND is_current = 1
        """,
        (row["repository"], row["pr_number"]),
    ).fetchone()
    if head is None:
        return False
    if (
        head["base_sha"] != row["base_sha"]
        or head["head_sha"] != row["head_sha"]
        or int(head["review_epoch"]) != int(row["review_epoch"])
        or head["state"] != row["state"]
    ):
        return False
    quality = connection.execute(
        """
        SELECT * FROM clawsweeper_quality
        WHERE repository = ? AND pr_number = ? AND base_sha = ?
          AND head_sha = ? AND review_epoch = ?
        """,
        (
            head["repository"],
            head["pr_number"],
            head["base_sha"],
            head["head_sha"],
            head["review_epoch"],
        ),
    ).fetchone()
    decision = orchestration.decide_orchestration_outcome(
        orchestration.outcome_from_review_row(
            head, quality, enrollment=trusted_enrollment
        )
    )
    eligibility = decision["notification"]["eligibility"]
    if eligibility not in {"merge_ready", "blocked"}:
        return False
    payload = json.loads(row["payload_json"])
    return pending_decision_matches_current(
        payload.get("orchestration_outcome"), decision
    )


def claimed_notification_still_current(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
    trusted_enrollment: Mapping[str, str],
) -> bool:
    """Revalidate a claimed row against the current state and complete decision.

    The claim/send fence binds the expected head state and canonical
    orchestration identity immediately before transport. A webhook that
    advances the same tuple after eligibility must retire the stale row.
    """
    return pending_review_notification_still_eligible(
        connection, row, trusted_enrollment
    )


def retire_notification(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
    *,
    required_status: str = "pending",
) -> bool:
    updated = connection.execute(
        """
        UPDATE notification_deliveries
        SET status = 'retired', last_error = ?, updated_at = ?
        WHERE event_key = ? AND channel = ? AND status = ?
        """,
        (
            NOTIFICATION_RETIRE_REASON,
            core.utc_now(),
            row["event_key"],
            row["channel"],
            required_status,
        ),
    )
    return updated.rowcount == 1


def retire_pending_notification(
    connection: sqlite3.Connection, row: sqlite3.Row
) -> bool:
    return retire_notification(connection, row, required_status="pending")


def suppressed_review_stages() -> dict[str, Any]:
    """Empty tick stages used when trusted enrollment is not Review Conductor."""
    skipped_bridge = {
        "schema": "smoky.review-conductor.bridge-drain.v1",
        "result": "skipped",
        "artifacts": [],
        "agent_polling": False,
        "merge_dispatched": False,
    }
    return {
        "bridges_before": skipped_bridge,
        "hydration": [],
        "worker": {
            "schema": "smoky.review-conductor.worker-wake.v1",
            "result": "skipped",
            "recovered": [],
            "actions": [],
            "failed_actions": [],
            "github_ci_polled": False,
            "merge_dispatched": False,
        },
        "openclaw": [],
        "clawsweeper": [],
        "bridges_after": skipped_bridge,
        "projection": {
            "schema": "smoky.review-conductor.projection.v1",
            "result": "skipped",
            "projected": [],
            "merge_authorized": False,
        },
    }


class OpenClawNotifier:
    def __init__(
        self,
        config: dict[str, Any],
        *,
        runner: CommandRunner = subprocess.run,
        environment: dict[str, str] | None = None,
    ) -> None:
        self.config = config
        self.runner = runner
        self.environment = os.environ if environment is None else environment

    def subprocess_environment(self) -> dict[str, str]:
        """Expose only ordinary runtime identity and locale to OpenClaw."""
        child = {
            "PATH": self.environment.get(
                "PATH", "/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
            ),
            "PYTHONUNBUFFERED": "1",
        }
        for name in ("HOME", "USER", "LOGNAME", "TMPDIR", "LC_ALL"):
            value = self.environment.get(name)
            if value:
                child[name] = value
        return child

    def command(self, channel: str, message: str) -> list[str]:
        notifications = self.config["notifications"]
        command = notifications["openclaw_path"]
        if channel == "openclaw_context":
            return [
                command,
                "system",
                "event",
                "--session-key",
                notifications["session_key"],
                "--mode",
                "next-heartbeat",
                "--text",
                message,
                "--json",
            ]
        target_env = notifications[f"{channel}_target_env"]
        target = self.environment.get(target_env)
        if not target:
            raise NotificationUnavailable(f"{channel} delivery target is unavailable")
        return [
            command,
            "message",
            "send",
            "--channel",
            channel,
            "--target",
            target,
            "--message",
            message,
            "--json",
        ]

    def send(self, channel: str, message: str) -> None:
        try:
            result = core.run_generation_bound(
                self.runner,
                self.command(channel, message),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=90,
                check=False,
                env=self.subprocess_environment(),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise UserlandError("OpenClaw notification outcome is uncertain") from exc
        if result.returncode != 0:
            raise UserlandError("OpenClaw notification delivery failed")
        receipt = parse_json_receipt(result.stdout, "OpenClaw notification")
        if not receipt:
            raise UserlandError("OpenClaw notification receipt is empty")


def deliver_notifications(
    config: dict[str, Any],
    notifier: OpenClawNotifier | Any,
    *,
    dry_run: bool,
    enrollment: Mapping[str, str] | None = None,
    authority_client: Any | None = None,
    authoritative: bool = False,
) -> dict[str, Any]:
    trusted_enrollment = orchestration.effective_trusted_enrollment(
        config, enrollment, authoritative=authoritative
    )
    routed = dict(config)
    owned = dict(routed.get("enrollment") or {})
    owned.update(trusted_enrollment)
    routed["enrollment"] = owned
    queue_notifications(routed, enrollment=trusted_enrollment, authoritative=True)
    connection = core.open_database(Path(config["paths"]["state_root"]), config["github_app"]["repository"])
    outcomes: list[dict[str, Any]] = []
    try:
        ensure_userland_tables(connection)
        rows = connection.execute(
            """
            SELECT * FROM notification_deliveries
            WHERE status = 'pending'
            ORDER BY created_at, event_key,
              CASE channel WHEN 'openclaw_context' THEN 0 WHEN 'discord' THEN 1 ELSE 2 END
            """
        ).fetchall()
        for row in rows:
            payload = json.loads(row["payload_json"])
            if not pending_review_notification_still_eligible(
                connection, row, trusted_enrollment
            ):
                if dry_run:
                    outcomes.append(
                        {
                            "event_key": row["event_key"],
                            "channel": row["channel"],
                            "result": "retired",
                        }
                    )
                    continue
                if retire_pending_notification(connection, row):
                    connection.commit()
                    outcomes.append(
                        {
                            "event_key": row["event_key"],
                            "channel": row["channel"],
                            "result": "retired",
                        }
                    )
                else:
                    connection.rollback()
                continue
            if dry_run:
                outcomes.append({"event_key": row["event_key"], "channel": row["channel"], "result": "planned"})
                continue
            # Operator alerts are intentionally unbound repository-level
            # maintenance. Review-result notifications carry and revalidate the
            # exact tuple that produced them.
            authority = (
                None if int(row["pr_number"]) == 0 else runtime.tuple_authority(row)
            )
            claimed = connection.execute(
                """
                UPDATE notification_deliveries
                SET status = 'uncertain', attempts = attempts + 1,
                  last_error = 'delivery in progress; reconcile if interrupted',
                  updated_at = ?
                WHERE event_key = ? AND channel = ? AND status = 'pending'
                """,
                (core.utc_now(), row["event_key"], row["channel"]),
            )
            if claimed.rowcount != 1:
                connection.rollback()
                continue
            # Commit the uncertain claim before any transport. A crash or SQLite
            # failure after a successful send can therefore never make this row
            # look retryable; an operator must reconcile the provider outcome.
            connection.commit()
            try:
                runtime.assert_authority(
                    authority_client,
                    f"notification:{row['event_key']}:{row['channel']}",
                    authority,
                )
            except core.AuthorityDenied as exc:
                restored = connection.execute(
                    """
                    UPDATE notification_deliveries
                    SET status = 'pending', attempts = attempts - 1,
                      last_error = ?, updated_at = ?
                    WHERE event_key = ? AND channel = ? AND status = 'uncertain'
                    """,
                    (str(exc)[:300], core.utc_now(), row["event_key"], row["channel"]),
                )
                connection.commit()
                if restored.rowcount != 1:
                    raise UserlandError(
                        "notification authority denial changed during recovery"
                    ) from exc
                raise
            # Bind the claim/send fence to the expected current state and
            # complete canonical decision immediately before transport.
            if not claimed_notification_still_current(
                connection, row, trusted_enrollment
            ):
                if retire_notification(
                    connection, row, required_status="uncertain"
                ):
                    connection.commit()
                    outcomes.append(
                        {
                            "event_key": row["event_key"],
                            "channel": row["channel"],
                            "result": "retired",
                        }
                    )
                else:
                    connection.rollback()
                continue
            try:
                notifier.send(row["channel"], payload["message"])
            except NotificationUnavailable as exc:
                connection.execute(
                    """
                    UPDATE notification_deliveries
                    SET status = 'pending', last_error = ?, updated_at = ?
                    WHERE event_key = ? AND channel = ? AND status = 'uncertain'
                    """,
                    (str(exc)[:300], core.utc_now(), row["event_key"], row["channel"]),
                )
                connection.commit()
                outcomes.append({"event_key": row["event_key"], "channel": row["channel"], "result": "not_ready"})
                continue
            except core.ContractError as exc:
                connection.execute(
                    """
                    UPDATE notification_deliveries
                    SET last_error = ?, updated_at = ?
                    WHERE event_key = ? AND channel = ? AND status = 'uncertain'
                    """,
                    (str(exc)[:300], core.utc_now(), row["event_key"], row["channel"]),
                )
                connection.commit()
                outcomes.append({"event_key": row["event_key"], "channel": row["channel"], "result": "uncertain"})
                continue
            connection.execute(
                """
                UPDATE notification_deliveries
                SET status = 'sent', last_error = NULL, updated_at = ?
                WHERE event_key = ? AND channel = ? AND status = 'uncertain'
                """,
                (core.utc_now(), row["event_key"], row["channel"]),
            )
            connection.commit()
            outcomes.append({"event_key": row["event_key"], "channel": row["channel"], "result": "sent"})
        return {
            "schema": "smoky.review-conductor.notifications.v1",
            "result": "planned" if dry_run else "processed",
            "deliveries": outcomes,
            "merge_dispatched": False,
        }
    finally:
        connection.close()


def run_tick(
    config: dict[str, Any],
    client: runtime.GitHubAppClient | Any | None,
    notifier: OpenClawNotifier | Any | None,
    *,
    dry_run: bool,
    enrollment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    profiles.require_enabled(config)
    if enrollment is None:
        trusted_enrollment = orchestration.resolve_trusted_enrollment(config)
    else:
        trusted_enrollment = orchestration.require_trusted_pair(enrollment)
    route, _reason = orchestration.enrollment_route(trusted_enrollment)
    if route == "review_conductor":
        first_bridge = (
            {"schema": "smoky.review-conductor.bridge-drain.v1", "result": "planned", "artifacts": []}
            if dry_run
            else runtime.drain_bridge_inboxes(config)
        )
        hydration = hydrate_pending_openclaw_heads(
            config, dry_run=dry_run, authority_client=client
        )
        worker = runtime.drain_actions(config, client, dry_run=dry_run)
        openclaw = collect_openclaw_terminals(config, dry_run=dry_run)
        clawsweeper = collect_clawsweeper_terminals(config, client, dry_run=dry_run)
        second_bridge = (
            {"schema": "smoky.review-conductor.bridge-drain.v1", "result": "planned", "artifacts": []}
            if dry_run
            else runtime.drain_bridge_inboxes(config)
        )
        projection = runtime.reconcile_projection(config, client, dry_run=dry_run)
    else:
        skipped = suppressed_review_stages()
        first_bridge = skipped["bridges_before"]
        hydration = skipped["hydration"]
        worker = skipped["worker"]
        openclaw = skipped["openclaw"]
        clawsweeper = skipped["clawsweeper"]
        second_bridge = skipped["bridges_after"]
        projection = skipped["projection"]
    notifications = deliver_notifications(
        config,
        notifier,
        dry_run=dry_run,
        enrollment=trusted_enrollment,
        authority_client=client,
        authoritative=True,
    )
    return {
        "schema": "smoky.review-conductor.userland-tick.v1",
        "result": "planned" if dry_run else "completed",
        "bridges_before": first_bridge,
        "hydration": hydration,
        "worker": worker,
        "openclaw": openclaw,
        "clawsweeper": clawsweeper,
        "bridges_after": second_bridge,
        "projection": projection,
        "notifications": notifications,
        "github_ci_polled": False,
        "merge_dispatched": False,
    }


def health(config: dict[str, Any]) -> dict[str, Any]:
    components: dict[str, dict[str, str]] = {}
    if config.get("enrollment", {}).get("enabled") is False:
        components["enrollment"] = runtime.component("not_ready", "; ".join(config["enrollment"]["blockers"]))
    for label, path in {
        "state_parent": Path(config["paths"]["state_root"]).parent,
        "proof_parent": Path(config["paths"]["proof_root"]).parent,
    }.items():
        ready = path.is_dir() and path.stat().st_uid == os.getuid()
        components[label] = runtime.component(
            "ready" if ready else "not_ready",
            "user-owned parent is ready" if ready else "user-owned parent is absent or not owned by the current user",
        )
    checkout = Path(config["paths"]["blocks_checkout"])
    checkout_ready = checkout.is_dir() and checkout.stat().st_uid == os.getuid()
    components["source_checkout" if config.get("profile_id") else "blocks_checkout"] = runtime.component(
        "ready" if checkout_ready else "not_ready",
        "same-user repository checkout is present" if checkout_ready else "same-user repository checkout is absent",
    )
    openclaw = Path(config["notifications"]["openclaw_path"])
    openclaw_ready = openclaw.is_file() and os.access(openclaw, os.X_OK)
    components["openclaw_gateway_cli"] = runtime.component(
        "ready" if openclaw_ready else "not_ready",
        "OpenClaw Gateway CLI is executable" if openclaw_ready else "OpenClaw Gateway CLI is unavailable",
    )
    return {
        "schema": "smoky.review-conductor.userland-health.v1",
        "overall": "ready" if all(item["state"] == "ready" for item in components.values()) else "not_ready",
        "components": components,
        "execution_mode": "current_user",
        "legacy_cp1_route": "disabled",
        "merge_authorized": False,
    }


def build_client(config: dict[str, Any]) -> runtime.GitHubAppClient:
    profiles.require_enabled(config)
    private_key = read_inherited_value(
        config["credentials"]["github_private_key_fd_env"],
        "GitHub App private key",
    )
    try:
        return runtime.GitHubAppClient(config, private_key)
    finally:
        private_key = ""


def serve(config: dict[str, Any]) -> None:
    profiles.require_enabled(config)
    webhook_secret = read_inherited_value(
        config["credentials"]["webhook_secret_fd_env"],
        "GitHub webhook secret",
    )
    client = build_client(config)
    notifier = OpenClawNotifier(config)
    stop = threading.Event()

    def worker_loop() -> None:
        while not stop.is_set():
            try:
                run_tick(config, client, notifier, dry_run=False)
            except core.ContractError:
                pass
            if stop.wait(config["worker"]["tick_seconds"]):
                return

    thread = threading.Thread(target=worker_loop, name="review-conductor-userland", daemon=True)
    thread.start()
    server = runtime.BoundedHTTPServer(
        (config["ingress"]["bind_host"], config["ingress"]["bind_port"]),
        runtime.build_http_handler(config, webhook_secret),
        request_timeout_seconds=config["ingress"]["request_timeout_seconds"],
    )
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        stop.set()
        server.server_close()
        thread.join(timeout=config["worker"]["tick_seconds"] + 1)
        webhook_secret = ""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--json", action="store_true")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("health")
    status_parser = subparsers.add_parser("status")
    status_parser.add_argument("--pr", type=int, required=True)
    retry_parser = subparsers.add_parser("retry-openclaw")
    retry_parser.add_argument("--pr", type=int, required=True)
    retry_mode = retry_parser.add_mutually_exclusive_group(required=True)
    retry_mode.add_argument("--dry-run", action="store_true")
    retry_mode.add_argument("--apply", action="store_true")
    reconcile_parser = subparsers.add_parser("reconcile-notification")
    reconcile_parser.add_argument("--pr", type=int, required=True)
    reconcile_parser.add_argument(
        "--channel",
        choices=("openclaw_context", "discord", "signal"),
        required=True,
    )
    reconcile_parser.add_argument(
        "--disposition", choices=("sent", "retry"), required=True
    )
    reconcile_parser.add_argument("--confirm", required=True)
    reconcile_mode = reconcile_parser.add_mutually_exclusive_group(required=True)
    reconcile_mode.add_argument("--dry-run", action="store_true")
    reconcile_mode.add_argument("--apply", action="store_true")
    tick_parser = subparsers.add_parser("tick")
    tick_mode = tick_parser.add_mutually_exclusive_group(required=True)
    tick_mode.add_argument("--dry-run", action="store_true")
    tick_mode.add_argument("--apply", action="store_true")
    serve_parser = subparsers.add_parser("serve")
    serve_parser.add_argument("--apply", action="store_true", required=True)
    adjudicate_parser = subparsers.add_parser("adjudicate")
    adjudicate_parser.add_argument("--event-file", type=Path, required=True)
    adjudicate_parser.add_argument("--apply", action="store_true", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_config(args.config)
        if config.get("profile_id") and args.command not in {"health", "status"}:
            raise UserlandError(
                "standalone profiles require tools/service_entrypoint.py and trusted admission"
            )
        if args.command not in {"health", "status"}:
            profiles.require_enabled(config)
        if args.command == "health":
            result = health(config)
        elif args.command == "status":
            result = core.status(
                argparse.Namespace(
                    config=Path(config["core_config"]),
                    state_root=Path(config["paths"]["state_root"]),
                    pr_number=args.pr,
                )
            )
        elif args.command == "retry-openclaw":
            result = retry_failed_openclaw(config, args.pr, apply=args.apply)
        elif args.command == "reconcile-notification":
            result = reconcile_uncertain_notification(
                config,
                args.pr,
                args.channel,
                args.disposition,
                args.confirm,
                apply=args.apply,
            )
        elif args.command == "tick":
            client = None if args.dry_run else build_client(config)
            notifier = OpenClawNotifier(config)
            result = run_tick(config, client, notifier, dry_run=args.dry_run)
        elif args.command == "adjudicate":
            result = core.ingest_internal_event(
                config_path=Path(config["core_config"]),
                state_root=Path(config["paths"]["state_root"]),
                event_payload=core.read_json(args.event_file, "adjudication event"),
            )
        else:
            serve(config)
            return 0
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except core.ContractError as exc:
        print(f"review-conductor-userland: {exc}", file=sys.stderr)
        return 4 if "gate" in str(exc).lower() or "human" in str(exc).lower() else 3


if __name__ == "__main__":
    raise SystemExit(main())
