#!/usr/bin/env python3
"""Current-user bootstrap and supervisor for the DinkusKit Review Conductor."""

from __future__ import annotations

import argparse
import contextlib
import getpass
import json
import os
import pwd
import re
import signal
import stat
import subprocess
import sys
import tempfile
import time
import warnings
from pathlib import Path
from typing import Any, Callable


TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
import review_conductor as core  # noqa: E402
import review_conductor_userland as userland  # noqa: E402
import review_conductor_profiles as profiles  # noqa: E402


BOOTSTRAP_CONFIRMATION = "cp1-userland-three-domain-bootstrap-v1"
CAPABILITIES = (
    "review-conductor.blocks.webhook-verify",
    "review-conductor.blocks.github-installation",
    "review-conductor.blocks.cloudflare-tunnel",
)
SERVICE_TOKEN_RE = re.compile(r"^[^\x00-\x20\x7f]{20,4096}$")
ACCOUNT_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class LauncherError(core.ContractError):
    """The current-user credential or process boundary is unavailable."""


Runner = Callable[..., subprocess.CompletedProcess[bytes]]
TokenReader = Callable[[str], str]


def clean_environment(config: dict[str, Any], *, service_token: str | None = None) -> dict[str, str]:
    try:
        user = pwd.getpwuid(os.getuid()).pw_name
    except KeyError as exc:
        raise LauncherError("current-user account identity is unavailable") from exc
    if ACCOUNT_NAME_RE.fullmatch(user) is None:
        raise LauncherError("current-user account identity has an invalid shape")
    environment = {
        "HOME": config["home"],
        "USER": user,
        "LOGNAME": user,
        "PATH": "/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        "TMPDIR": "/tmp",
        "LC_ALL": "C",
    }
    if service_token is not None:
        environment["OP_BIOMETRIC_UNLOCK_ENABLED"] = "false"
        environment["OP_LOAD_DESKTOP_APP_SETTINGS"] = "false"
        environment["OP_SERVICE_ACCOUNT_TOKEN"] = service_token
    return environment


def bootstrap_bytes(path: Path) -> bytes:
    try:
        parent = path.parent.lstat()
        metadata = path.lstat()
    except OSError as exc:
        raise LauncherError("current-user service-account bootstrap is unavailable") from exc
    if (
        not stat.S_ISDIR(parent.st_mode)
        or stat.S_ISLNK(parent.st_mode)
        or parent.st_uid != os.getuid()
        or stat.S_IMODE(parent.st_mode) != 0o700
    ):
        raise LauncherError("bootstrap parent must be a current-user mode 0700 directory")
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o400
        or metadata.st_nlink != 1
    ):
        raise LauncherError("bootstrap must be a current-user single-link mode 0400 file")
    try:
        value = path.read_bytes()
    except OSError as exc:
        raise LauncherError("current-user service-account bootstrap could not be read") from exc
    if not value.endswith(b"\n") or value.count(b"\n") != 1:
        raise LauncherError("bootstrap must contain exactly one non-empty line")
    token = value[:-1]
    try:
        decoded = token.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise LauncherError("bootstrap is not UTF-8") from exc
    if SERVICE_TOKEN_RE.fullmatch(decoded) is None:
        raise LauncherError("bootstrap has an invalid bounded token shape")
    return token


def bootstrap_status(config: dict[str, Any]) -> dict[str, Any]:
    if config.get("enrollment", {}).get("enabled") is False:
        return {"schema": "smoky.review-conductor.userland-bootstrap-status.v1", "result": "waiting_for_human", "domains": {}, "blockers": config["enrollment"]["blockers"], "values_exposed": False, "merge_authorized": False}
    domains: dict[str, dict[str, str]] = config["onepassword"]["domains"]
    states: dict[str, str] = {}
    for capability in profiles.capabilities(config):
        try:
            bootstrap_bytes(Path(domains[capability]["bootstrap_file"]))
        except core.ContractError:
            states[capability] = "not_ready"
        else:
            states[capability] = "ready"
    ready = all(value == "ready" for value in states.values())
    return {
        "schema": "smoky.review-conductor.userland-bootstrap-status.v1",
        "result": "ready" if ready else "waiting_for_human",
        "auth_mode": "three_distinct_service_accounts",
        "domains": states,
        "desktop_authorization_required_per_pr": False,
        "merge_authorized": False,
        "values_exposed": False,
    }


def read_attended_tokens(token_reader: TokenReader, config: dict[str, Any] | None = None) -> dict[str, bytes]:
    values: dict[str, bytes] = {}
    for capability in profiles.capabilities(config or {}):
        try:
            value = token_reader(f"{capability} service-account token: ")
        except (EOFError, KeyboardInterrupt) as exc:
            raise LauncherError("attended service-account token entry did not complete") from exc
        if SERVICE_TOKEN_RE.fullmatch(value) is None:
            raise LauncherError("attended service-account token has an invalid shape")
        values[capability] = value.encode("utf-8")
        value = ""
    if len(set(values.values())) != len(values):
        raise LauncherError("1Password service-account bootstraps must be distinct")
    return values


def read_attended_token(prompt: str) -> str:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            return getpass.getpass(prompt)
    except getpass.GetPassWarning as exc:
        raise LauncherError("secure no-echo terminal input is unavailable") from exc


def validate_service_account_scope(
    config: dict[str, Any],
    capability: str,
    token: bytes,
    *,
    runner: Runner = subprocess.run,
) -> None:
    profiles.require_enabled(config)
    domain = config["onepassword"]["domains"][capability]
    try:
        decoded_token = token.decode("utf-8")
        result = runner(
            [config["onepassword"]["op_path"], "vault", "list", "--format", "json"],
            env=clean_environment(config, service_token=decoded_token),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=30,
            check=False,
        )
    except (UnicodeDecodeError, OSError, subprocess.TimeoutExpired) as exc:
        raise LauncherError("1Password service-account scope check failed") from exc
    finally:
        decoded_token = "" if "decoded_token" in locals() else ""
    if result.returncode != 0:
        raise LauncherError("1Password service-account scope check failed")
    try:
        vaults = json.loads(result.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LauncherError("1Password service-account scope check was invalid") from exc
    if (
        not isinstance(vaults, list)
        or len(vaults) != 1
        or not isinstance(vaults[0], dict)
        or vaults[0].get("name") != domain["runtime_vault"]
    ):
        raise LauncherError("1Password service account is not scoped to its one exact vault")


def write_bootstrap_batch(config: dict[str, Any], values: dict[str, bytes]) -> None:
    profiles.require_enabled(config)
    root = Path(config["onepassword"]["bootstrap_root"])
    if root.exists():
        metadata = root.lstat()
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o700
        ):
            raise LauncherError("bootstrap root is not a current-user mode 0700 directory")
    else:
        root.mkdir(mode=0o700, parents=True)
    targets = {
        capability: Path(config["onepassword"]["domains"][capability]["bootstrap_file"])
        for capability in profiles.capabilities(config)
    }
    if any(path.exists() or path.is_symlink() for path in targets.values()):
        raise LauncherError("a current-user service-account bootstrap already exists")
    created: list[Path] = []
    with tempfile.TemporaryDirectory(prefix=".stage-", dir=root) as stage_name:
        stage = Path(stage_name)
        os.chmod(stage, 0o700)
        staged: dict[str, Path] = {}
        for capability in profiles.capabilities(config):
            temporary = stage / targets[capability].name
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o400,
            )
            try:
                os.write(descriptor, values[capability] + b"\n")
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            staged[capability] = temporary
        try:
            for capability in profiles.capabilities(config):
                target = targets[capability]
                os.replace(staged[capability], target)
                created.append(target)
            for target in created:
                bootstrap_bytes(target)
        except Exception:
            for target in created:
                with contextlib.suppress(OSError):
                    target.unlink()
            raise


def bootstrap(
    config: dict[str, Any],
    *,
    apply: bool,
    attended: bool,
    confirmation: str | None,
    runner: Runner = subprocess.run,
    token_reader: TokenReader | None = None,
) -> dict[str, Any]:
    profiles.require_enabled(config)
    current = bootstrap_status(config)
    if current["result"] == "ready":
        return {**current, "result": "already_ready", "mutation_performed": False}
    if not apply:
        return {
            **current,
            "result": "ready_for_attended_three_paste_bootstrap",
            "confirmation": BOOTSTRAP_CONFIRMATION,
            "mutation_performed": False,
        }
    if not attended or confirmation != BOOTSTRAP_CONFIRMATION:
        raise LauncherError("current-user bootstrap requires the exact attended confirmation")
    if any(key.startswith("OP_") for key in os.environ):
        raise LauncherError("ambient 1Password authentication state is forbidden")
    if token_reader is None:
        token_reader = read_attended_token
    values = read_attended_tokens(token_reader, config)
    try:
        for capability in profiles.capabilities(config):
            validate_service_account_scope(config, capability, values[capability], runner=runner)
        write_bootstrap_batch(config, values)
    finally:
        values.clear()
    return {
        **bootstrap_status(config),
        "result": "bootstrapped",
        "desktop_authorization_batches": 0,
        "manual_secret_entries": len(profiles.capabilities(config)),
        "mutation_performed": True,
        "values_exposed": False,
    }


def resolve_runtime_value(
    config: dict[str, Any],
    capability: str,
    *,
    runner: Runner = subprocess.run,
) -> bytes:
    profiles.require_enabled(config)
    domain = config["onepassword"]["domains"][capability]
    token = bootstrap_bytes(Path(domain["bootstrap_file"]))
    try:
        decoded_token = token.decode("utf-8")
        result = runner(
            [config["onepassword"]["op_path"], "read", domain["runtime_reference"]],
            env=clean_environment(config, service_token=decoded_token),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=30,
            check=False,
        )
    except (UnicodeDecodeError, OSError, subprocess.TimeoutExpired) as exc:
        raise LauncherError("1Password runtime resolver failed") from exc
    finally:
        decoded_token = "" if "decoded_token" in locals() else ""
    if result.returncode != 0 or not result.stdout:
        raise LauncherError("1Password runtime resolver failed")
    value = result.stdout[:-1] if result.stdout.endswith(b"\n") else result.stdout
    if not value or len(value) > 1024 * 1024 or b"\x00" in value:
        raise LauncherError("1Password runtime value has an invalid bounded shape")
    if capability == profiles.capabilities(config)[1] and not value.startswith(
        b"-----BEGIN"
    ):
        raise LauncherError("GitHub App private key attachment is not PEM")
    if capability != profiles.capabilities(config)[1] and b"\n" in value:
        raise LauncherError("runtime bearer value must be one bounded line")
    return value


def credential_descriptor(value: bytes) -> int:
    """Return a bounded, rewound, anonymous file descriptor (never a filled pipe).

    TemporaryFile uses O_TMPFILE where available, otherwise unlinks before it
    returns. Verify anonymity *before* writing any value; unsupported hosts fail
    closed. This may use disk-backed anonymous storage, not guaranteed RAM-only
    storage. No credential is written to a persistent named file.
    """
    if not isinstance(value, bytes) or not value or len(value) > 1024 * 1024:
        raise LauncherError("runtime descriptor value has an invalid bounded shape")
    try:
        with tempfile.TemporaryFile(mode="w+b") as stream:
            metadata = os.fstat(stream.fileno())
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 0:
                raise LauncherError("anonymous runtime descriptor is unavailable")
            os.fchmod(stream.fileno(), 0o600)
            if stream.write(value) != len(value):
                raise LauncherError("runtime descriptor write was incomplete")
            stream.flush()
            stream.seek(0)
            # dup is non-inheritable; Popen grants only the explicit pass_fds.
            return os.dup(stream.fileno())
    except OSError:
        raise LauncherError("anonymous runtime descriptor preparation failed") from None


def stop_child(process: Any) -> None:
    """Bound graceful shutdown, then kill and reap a surviving child."""
    if process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def child_environment(config: dict[str, Any], webhook_fd: int, github_fd: int) -> dict[str, str]:
    environment = clean_environment(config)
    environment["PYTHONUNBUFFERED"] = "1"
    environment[config["credentials"]["webhook_secret_fd_env"]] = str(webhook_fd)
    environment[config["credentials"]["github_private_key_fd_env"]] = str(github_fd)
    for name in (
        config["notifications"]["discord_target_env"],
        config["notifications"]["signal_target_env"],
    ):
        if os.environ.get(name):
            environment[name] = os.environ[name]
    if any(key.startswith("OP_") for key in environment):
        raise LauncherError("runtime consumer environment contains 1Password auth state")
    return environment


def start(
    config: dict[str, Any],
    *,
    config_path: Path,
    popen: Any = subprocess.Popen,
) -> int:
    profiles.require_enabled(config)
    if bootstrap_status(config)["result"] != "ready":
        raise LauncherError("current-user service-account bootstrap is not ready")
    conductor = None
    tunnel = None
    # Register each descriptor immediately, so partial preparation/spawn failure
    # closes everything already acquired. No child exists while values are written.
    with contextlib.ExitStack() as descriptors:
        prepared = []
        for capability in profiles.capabilities(config):
            value = resolve_runtime_value(config, capability)
            try:
                descriptor = credential_descriptor(value)
            finally:
                value = b""
            descriptors.callback(os.close, descriptor)
            prepared.append(descriptor)
        webhook_fd, github_fd, connector_fd = prepared
        try:
            conductor = popen(
                [
                    sys.executable,
                    str(ROOT / "tools/review_conductor_userland.py"),
                    "--config",
                    str(config_path),
                    "serve",
                    "--apply",
                ],
                cwd=config["source_root"],
                env=child_environment(config, webhook_fd, github_fd),
                pass_fds=(webhook_fd, github_fd),
            )
            tunnel = popen(
                [
                    config["tunnel"]["cloudflared_path"],
                    "tunnel",
                    "--no-autoupdate",
                    "run",
                    "--token-file",
                    f"/dev/fd/{connector_fd}",
                ],
                cwd=config["home"],
                env=clean_environment(config),
                pass_fds=(connector_fd,),
            )
        except BaseException:
            if conductor is not None:
                stop_child(conductor)
            raise
    assert conductor is not None and tunnel is not None
    previous_handlers: dict[int, Any] = {}

    def request_stop(_signum: int, _frame: Any) -> None:
        for process in (tunnel, conductor):
            if process.poll() is None:
                process.terminate()

    for signum in (signal.SIGINT, signal.SIGTERM):
        previous_handlers[signum] = signal.signal(signum, request_stop)
    try:
        while conductor.poll() is None and tunnel.poll() is None:
            time.sleep(1)
    finally:
        request_stop(signal.SIGTERM, None)
        for process in (tunnel, conductor):
            stop_child(process)
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
    conductor_code = conductor.returncode or 0
    tunnel_code = tunnel.returncode or 0
    return conductor_code if conductor_code != 0 else tunnel_code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=userland.DEFAULT_CONFIG)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("preflight")
    bootstrap_parser = subparsers.add_parser("bootstrap")
    mode = bootstrap_parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    bootstrap_parser.add_argument("--attended", action="store_true")
    bootstrap_parser.add_argument("--confirm")
    start_parser = subparsers.add_parser("start")
    start_parser.add_argument("--apply", action="store_true", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        try:
            config_path = args.config.resolve(strict=True)
        except OSError as exc:
            raise LauncherError("current-user config path is unavailable") from exc
        config = userland.load_config(config_path)
        if args.command == "preflight":
            result = {
                "schema": "smoky.review-conductor.userland-launcher-preflight.v1",
                "bootstrap": bootstrap_status(config),
                "conductor": userland.health(config),
                "execution_mode": "cp1-current-user",
                "sudo_required_per_pr": False,
                "desktop_authorization_required_per_pr": False,
                "merge_authorized": False,
            }
        elif args.command == "bootstrap":
            result = bootstrap(
                config,
                apply=args.apply,
                attended=args.attended,
                confirmation=args.confirm,
            )
        else:
            return start(config, config_path=config_path)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except core.ContractError as exc:
        print(f"review-conductor-userland-launcher: {exc}", file=sys.stderr)
        return 4 if "attended" in str(exc).lower() or "human" in str(exc).lower() else 3


if __name__ == "__main__":
    raise SystemExit(main())
