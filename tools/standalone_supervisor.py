#!/usr/bin/env python3
"""Fail-closed foreground supervisor for one standalone Review Conductor profile."""
from __future__ import annotations

import argparse
import contextlib
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import stat
import subprocess
import sys
import time
from typing import Any, Callable

import review_conductor as core
import review_conductor_profiles as profiles
import review_conductor_userland as userland
import review_conductor_userland_launcher as legacy_launcher

ROOT = Path(__file__).resolve().parents[1]
SERVICE_ENTRYPOINT = ROOT / "tools/service_entrypoint.py"
CONTROL_SOCKET_NAME = ".standalone-supervisor.sock"
SUPERVISOR_LOCK_NAME = ".standalone-supervisor.lock"
MAX_CONTROL_BYTES = 4096
GROUP_SHUTDOWN_SECONDS = 10


class SupervisorError(core.ContractError):
    """The standalone process or local control boundary is unavailable."""


def load_profile(profile_path: Path) -> dict[str, Any]:
    try:
        resolved = profile_path.resolve(strict=True)
    except OSError as exc:
        raise SupervisorError("standalone profile is unavailable") from exc
    config = userland.load_config(resolved)
    if config.get("profile_id") != "openclaw-smcbd-suite":
        raise SupervisorError("standalone supervisor accepts only the isolated SMCBD profile")
    return config


def require_registry_path(registry_path: Path) -> Path:
    if not registry_path.is_absolute():
        raise SupervisorError("standalone registry path must be absolute")
    return registry_path


def supervisor_paths(config: dict[str, Any]) -> tuple[Path, Path]:
    state_root = Path(config["paths"]["state_root"])
    return state_root / SUPERVISOR_LOCK_NAME, state_root / CONTROL_SOCKET_NAME


def configuration_identity(
    config: dict[str, Any], profile_path: Path, registry_path: Path
) -> str:
    """Bind local control to the exact non-secret tenant/process boundary."""
    paths = config["paths"]
    app = config["github_app"]
    config_digest = hashlib.sha256(
        json.dumps(config, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()
    document = {
        "schema": "review-conductor.standalone-supervisor-identity.v1",
        "profile_path": str(profile_path.resolve(strict=True)),
        "registry_path": str(require_registry_path(registry_path)),
        "config_sha256": config_digest,
        "profile_id": config["profile_id"],
        "repository": app["repository"],
        "repository_id": app["repository_id"],
        "app_id": app["app_id"],
        "installation_id": app["installation_id"],
        "state_root": str(Path(paths["state_root"])),
        "checkout_root": str(Path(paths["blocks_checkout"])),
        "proof_root": str(Path(paths["proof_root"])),
        "bind_host": config["ingress"]["bind_host"],
        "bind_port": config["ingress"]["bind_port"],
    }
    encoded = json.dumps(document, separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def _source_descriptor(config: dict[str, Any], key: str) -> int:
    env_name = config["credentials"][key]
    raw = os.environ.get(env_name)
    if raw is None or not raw.isascii() or not raw.isdigit() or int(raw) < 3:
        raise SupervisorError("required inherited credential descriptor is unavailable")
    descriptor = int(raw)
    try:
        os.fstat(descriptor)
    except OSError as exc:
        raise SupervisorError("required inherited credential descriptor is unavailable") from exc
    return descriptor


def _close_descriptor(descriptor: int) -> None:
    with contextlib.suppress(OSError):
        os.close(descriptor)


def prepare_credentials(
    config: dict[str, Any], stack: contextlib.ExitStack
) -> tuple[int, int]:
    """Copy inherited values into bounded anonymous files retained for restart."""
    keys = ("webhook_secret_fd_env", "github_private_key_fd_env")
    prepared: list[int] = []
    labels = ("GitHub webhook secret", "GitHub App private key")
    with contextlib.ExitStack() as incoming:
        sources: list[int] = []
        for key in keys:
            descriptor = _source_descriptor(config, key)
            if descriptor in sources:
                raise SupervisorError("standalone credentials require distinct descriptors")
            incoming.callback(_close_descriptor, descriptor)
            sources.append(descriptor)
        for key, label in zip(keys, labels):
            value = userland.read_inherited_value(config["credentials"][key], label)
            try:
                descriptor = legacy_launcher.credential_descriptor(value.encode("utf-8"))
            finally:
                value = ""
            stack.callback(_close_descriptor, descriptor)
            prepared.append(descriptor)
    return prepared[0], prepared[1]


def child_environment(
    config: dict[str, Any], credentials: tuple[int, int]
) -> dict[str, str]:
    environment = legacy_launcher.clean_environment(config)
    environment["PYTHONUNBUFFERED"] = "1"
    for key, descriptor in zip(
        ("webhook_secret_fd_env", "github_private_key_fd_env"), credentials
    ):
        environment[config["credentials"][key]] = str(descriptor)
    for name in (
        config["notifications"]["discord_target_env"],
        config["notifications"]["signal_target_env"],
    ):
        if os.environ.get(name):
            environment[name] = os.environ[name]
    if any(name.startswith("OP_") for name in environment):
        raise SupervisorError("service environment contains credential-resolver state")
    return environment


def spawn_service(
    config: dict[str, Any],
    profile_path: Path,
    registry_path: Path,
    credentials: tuple[int, int],
    *,
    popen: Callable[..., Any] = subprocess.Popen,
) -> Any:
    for descriptor in credentials:
        try:
            os.lseek(descriptor, 0, os.SEEK_SET)
        except OSError as exc:
            raise SupervisorError("credential descriptor cannot be rewound for service start") from exc
    command = [
        sys.executable,
        str(SERVICE_ENTRYPOINT),
        "--profile",
        str(profile_path),
        "--registry",
        str(registry_path),
        "--apply",
        "serve",
    ]
    try:
        return popen(
            command,
            cwd=str(ROOT),
            env=child_environment(config, credentials),
            pass_fds=credentials,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        raise SupervisorError("standalone service could not be started") from exc


def _missing_process_group(exc: BaseException) -> bool:
    return isinstance(exc, ProcessLookupError) or (
        isinstance(exc, OSError) and getattr(exc, "errno", None) == errno.ESRCH
    )


def process_group_exists(
    pgid: int, *, kill_group: Callable[[int, int], None]
) -> bool:
    try:
        kill_group(pgid, 0)
    except OSError as exc:
        if _missing_process_group(exc):
            return False
        raise
    return True


def stop_service_process(
    process: Any,
    *,
    kill_group: Callable[[int, int], None] | None = None,
    timeout: float = GROUP_SHUTDOWN_SECONDS,
) -> None:
    """Bound shutdown of the service-owned process group, independent of leader state."""
    if kill_group is None:
        kill_group = os.killpg
    pgid = process.pid
    try:
        kill_group(pgid, signal.SIGTERM)
    except OSError as exc:
        if not _missing_process_group(exc):
            raise
    deadline = time.monotonic() + timeout
    while process_group_exists(pgid, kill_group=kill_group):
        if time.monotonic() >= deadline:
            try:
                kill_group(pgid, signal.SIGKILL)
            except OSError as exc:
                if _missing_process_group(exc):
                    break
                raise
            kill_deadline = time.monotonic() + timeout
            while process_group_exists(pgid, kill_group=kill_group):
                if time.monotonic() >= kill_deadline:
                    break
                time.sleep(0.05)
            break
        time.sleep(0.05)
    with contextlib.suppress(
        ChildProcessError, ProcessLookupError, subprocess.TimeoutExpired
    ):
        process.wait(timeout=1)


class Supervisor:
    def __init__(
        self,
        config: dict[str, Any],
        profile_path: Path,
        registry_path: Path,
        credentials: tuple[int, int],
        identity: str,
        *,
        popen: Callable[..., Any] = subprocess.Popen,
    ) -> None:
        self.config = config
        self.profile_path = profile_path
        self.registry_path = registry_path
        self.credentials = credentials
        self.identity = identity
        self.popen = popen
        self.child: Any | None = None
        self.generation = 0
        self.stopping = False

    def start_child(self) -> None:
        if self.child is not None and self.child.poll() is None:
            raise SupervisorError("standalone service is already running")
        self.child = spawn_service(
            self.config,
            self.profile_path,
            self.registry_path,
            self.credentials,
            popen=self.popen,
        )
        self.generation += 1

    def stop_child(self) -> None:
        if self.child is not None:
            stop_service_process(self.child)

    def restart_child(self) -> None:
        self.stop_child()
        self.child = None
        self.start_child()

    def snapshot(self) -> dict[str, Any]:
        returncode = None if self.child is None else self.child.poll()
        if self.stopping:
            status = "stopping"
        elif self.child is None:
            status = "failed"
        elif returncode is None:
            status = "running"
        else:
            status = "failed"
        return {
            "schema": "review-conductor.standalone-supervisor-health.v1",
            "status": status,
            "identity": self.identity,
            "profile_id": self.config["profile_id"],
            "repository": self.config["github_app"]["repository"],
            "bind_host": self.config["ingress"]["bind_host"],
            "bind_port": self.config["ingress"]["bind_port"],
            "supervisor_pid": os.getpid(),
            "service_pid": None if self.child is None else self.child.pid,
            "service_returncode": returncode,
            "generation": self.generation,
            "automatic_restart": False,
        }


def _recv_line(connection: socket.socket, label: str) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while total <= MAX_CONTROL_BYTES:
        chunk = connection.recv(min(1024, MAX_CONTROL_BYTES + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if b"\n" in chunk:
            break
    raw = b"".join(chunks)
    if len(raw) > MAX_CONTROL_BYTES or not raw.endswith(b"\n") or raw.count(b"\n") != 1:
        raise SupervisorError(f"invalid standalone supervisor {label}")
    return raw


def _read_request(connection: socket.socket) -> dict[str, Any]:
    raw = _recv_line(connection, "control request")
    try:
        request = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SupervisorError("invalid standalone supervisor control request") from exc
    if not isinstance(request, dict) or set(request) != {"command", "identity"}:
        raise SupervisorError("invalid standalone supervisor control request")
    if request["command"] not in {"health", "stop", "restart"}:
        raise SupervisorError("unsupported standalone supervisor control request")
    return request


def _send_response(connection: socket.socket, response: dict[str, Any]) -> None:
    connection.sendall(
        json.dumps(response, separators=(",", ":"), sort_keys=True).encode() + b"\n"
    )


def _handle_control(connection: socket.socket, supervisor: Supervisor) -> bool:
    try:
        request = _read_request(connection)
        if request["identity"] != supervisor.identity:
            raise SupervisorError("standalone supervisor identity does not match")
        if request["command"] == "restart":
            supervisor.restart_child()
        elif request["command"] == "stop":
            supervisor.stopping = True
            _send_response(connection, supervisor.snapshot())
            return False
        _send_response(connection, supervisor.snapshot())
    except SupervisorError as exc:
        with contextlib.suppress(OSError):
            _send_response(connection, {"status": "rejected", "error": str(exc)})
    except OSError:
        # A stalled or disconnected local client cannot terminate the service.
        with contextlib.suppress(OSError):
            _send_response(
                connection,
                {"status": "rejected", "error": "control transport failed"},
            )
    return True


@contextlib.contextmanager
def supervisor_lock(lock_path: Path):
    try:
        state_root = core.ensure_state_root(lock_path.parent)
    except core.ContractError as exc:
        raise SupervisorError("standalone supervisor state root is unavailable") from exc
    lock_path = state_root / lock_path.name
    try:
        descriptor = os.open(
            lock_path,
            os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW,
            0o600,
        )
    except OSError as exc:
        raise SupervisorError("standalone supervisor lock is unavailable") from exc
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_nlink != 1
        ):
            raise SupervisorError("standalone supervisor lock is not a private regular file")
        os.fchmod(descriptor, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise SupervisorError("standalone supervisor is already active") from exc
        yield
    finally:
        os.close(descriptor)


def run_supervisor(
    config: dict[str, Any],
    profile_path: Path,
    registry_path: Path,
    *,
    popen: Callable[..., Any] = subprocess.Popen,
    install_signals: bool = True,
) -> int:
    profiles.require_enabled(config)
    identity = configuration_identity(config, profile_path, registry_path)
    lock_path, socket_path = supervisor_paths(config)
    with supervisor_lock(lock_path), contextlib.ExitStack() as stack:
        credentials = prepare_credentials(config, stack)
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        stack.callback(listener.close)
        try:
            socket_path.unlink(missing_ok=True)
            listener.bind(str(socket_path))
            os.chmod(socket_path, 0o600)
            stack.callback(socket_path.unlink, missing_ok=True)
            listener.listen(4)
            listener.settimeout(0.25)
        except OSError as exc:
            raise SupervisorError("standalone supervisor control socket is unavailable") from exc
        supervisor = Supervisor(
            config, profile_path, registry_path, credentials, identity, popen=popen
        )
        previous_handlers: dict[int, Any] = {}

        def request_stop(_signum: int, _frame: Any) -> None:
            supervisor.stopping = True

        try:
            if install_signals:
                for signum in (signal.SIGINT, signal.SIGTERM):
                    previous_handlers[signum] = signal.signal(signum, request_stop)
            # Install handlers before spawning so a startup-time signal cannot
            # leave the service child running without its foreground supervisor.
            supervisor.start_child()
            while not supervisor.stopping:
                try:
                    connection, _ = listener.accept()
                except TimeoutError:
                    continue
                with connection:
                    connection.settimeout(2)
                    if not _handle_control(connection, supervisor):
                        break
        finally:
            supervisor.stopping = True
            supervisor.stop_child()
            for signum, handler in previous_handlers.items():
                signal.signal(signum, handler)
    return 0


def request_control(
    config: dict[str, Any], profile_path: Path, registry_path: Path, command: str
) -> dict[str, Any]:
    identity = configuration_identity(config, profile_path, registry_path)
    _, socket_path = supervisor_paths(config)
    request = json.dumps(
        {"command": command, "identity": identity},
        separators=(",", ":"),
        sort_keys=True,
    ).encode() + b"\n"
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(2)
            connection.connect(str(socket_path))
            connection.sendall(request)
            raw = _recv_line(connection, "response")
    except (FileNotFoundError, ConnectionRefusedError):
        return {
            "schema": "review-conductor.standalone-supervisor-health.v1",
            "status": "stopped",
            "identity": identity,
            "profile_id": config["profile_id"],
            "repository": config["github_app"]["repository"],
            "automatic_restart": False,
        }
    except OSError as exc:
        raise SupervisorError("standalone supervisor control request failed") from exc
    try:
        response = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SupervisorError("standalone supervisor returned an invalid response") from exc
    if not isinstance(response, dict):
        raise SupervisorError("standalone supervisor returned an invalid response")
    status = response.get("status")
    if status not in {"running", "failed", "stopping", "rejected"}:
        raise SupervisorError("standalone supervisor returned an invalid response")
    if status == "rejected":
        if set(response) != {"status", "error"} or not isinstance(
            response["error"], str
        ):
            raise SupervisorError("standalone supervisor returned an invalid response")
    elif (
        response.get("schema") != "review-conductor.standalone-supervisor-health.v1"
        or response.get("identity") != identity
    ):
        raise SupervisorError("standalone supervisor returned a mismatched identity")
    return response


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--registry", type=Path, required=True)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("health")
    for command in ("start", "stop", "restart"):
        child = subparsers.add_parser(command)
        child.add_argument("--apply", action="store_true", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        profile_path = args.profile.resolve(strict=True)
        registry_path = require_registry_path(args.registry)
        config = load_profile(profile_path)
        if args.command == "start":
            return run_supervisor(config, profile_path, registry_path)
        response = request_control(config, profile_path, registry_path, args.command)
        if args.command == "restart" and response.get("status") == "stopped":
            raise SupervisorError("standalone supervisor is not active")
        print(json.dumps(response, indent=2, sort_keys=True))
        return 0 if response.get("status") != "rejected" else 2
    except (core.ContractError, OSError, ValueError) as exc:
        print(f"review-conductor-standalone-supervisor: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
