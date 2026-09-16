#!/usr/bin/env python3
"""Inactive standalone service entrypoint; activation requires external state."""
from __future__ import annotations

import argparse
import contextlib
from contextlib import contextmanager
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import select
import signal
import sqlite3
import stat
import sys
import threading
import time
from typing import Any, Callable, Iterable

import review_conductor as core
import review_conductor_profiles as profiles
import review_conductor_runtime as runtime
import review_conductor_userland as userland
import service_runtime as service
import trusted_admission as admission

ROOT = Path(__file__).resolve().parents[1]
REGISTRY_MODE = 0o600
PROFILE_DIGEST_ENV = "REVIEW_CONDUCTOR_EXPECTED_PROFILE_SHA256"
GENERATION_FD_ENV = "REVIEW_CONDUCTOR_GENERATION_FD"
LEADER_FD_ENV = "REVIEW_CONDUCTOR_LEADER_FD"
PARENT_LIFETIME_FD_ENV = "REVIEW_CONDUCTOR_PARENT_LIFETIME_FD"
OWNED_GENERATION_SHUTDOWN_SECONDS = 10
STOP_SIGNALS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)


def profile_config_digest(config: dict) -> str:
    return hashlib.sha256(
        json.dumps(config, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()


def _require_inherited_descriptor(raw: str | None, label: str) -> int:
    if raw is None or not raw.isascii() or not raw.isdigit() or int(raw) < 3:
        raise service.ServiceError(f"{label} is unavailable")
    descriptor = int(raw)
    try:
        os.fstat(descriptor)
    except OSError as exc:
        raise service.ServiceError(f"{label} is unavailable") from exc
    return descriptor


def parent_lifetime_lost(parent_fd: int) -> bool:
    """Return true only after the supervisor's parent-lifetime write end closes."""
    try:
        readable, _, _ = select.select([parent_fd], [], [], 0)
        if not readable:
            return False
        value = os.read(parent_fd, 1)
    except OSError as exc:
        raise service.ServiceError("service parent-lifetime descriptor is unavailable") from exc
    if value:
        raise service.ServiceError("service parent-lifetime descriptor was corrupted")
    return True


def drain_owned_session(
    timeout: float | None = None,
    *,
    kill_group: Callable[[int, int], None] | None = None,
) -> None:
    """Bound-drain this process session only while it still owns the group."""
    if timeout is None:
        timeout = OWNED_GENERATION_SHUTDOWN_SECONDS
    if kill_group is None:
        kill_group = os.killpg
    pid = os.getpid()
    pgid = os.getpgrp()
    if pgid != pid:
        raise service.ServiceError("service is not the owned session leader")
    try:
        kill_group(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except OSError as exc:
        raise service.ServiceError("owned service generation could not be signaled") from exc
    deadline = time.monotonic() + timeout
    while _owned_children_remain() and time.monotonic() < deadline:
        time.sleep(0.05)
    # Parent-loss cannot observe the supervisor's generation-read EOF, so always
    # escalate while this process still owns the session. A reparented or
    # TERM-resistant descendant stays in that group until SIGKILL.
    try:
        kill_group(pgid, signal.SIGKILL)
    except ProcessLookupError:
        return
    except OSError as exc:
        raise service.ServiceError("owned service generation could not be signaled") from exc
    kill_deadline = time.monotonic() + timeout
    while _owned_children_remain() and time.monotonic() < kill_deadline:
        time.sleep(0.05)


def _owned_children_remain() -> bool:
    while True:
        try:
            finished, _status = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return False
        if finished == 0:
            return True


def load_supervised_profile(profile_path: Path) -> dict:
    """Load exactly the profile configuration approved by the supervisor."""
    expected = os.environ.get(PROFILE_DIGEST_ENV)
    generation_raw = os.environ.get(GENERATION_FD_ENV)
    leader_raw = os.environ.get(LEADER_FD_ENV)
    parent_raw = os.environ.get(PARENT_LIFETIME_FD_ENV)
    config = userland.load_config(profile_path)
    if (
        expected is None
        and generation_raw is None
        and leader_raw is None
        and parent_raw is None
    ):
        # Direct source qualification and maintenance callers have no supervisor
        # identity to compare. The standalone supervisor always supplies all four.
        return config
    if (
        expected is None
        or len(expected) != 64
        or expected != expected.lower()
        or any(character not in "0123456789abcdef" for character in expected)
    ):
        raise service.ServiceError("expected supervised profile digest is unavailable")
    _require_inherited_descriptor(generation_raw, "service generation descriptor")
    try:
        leader_fd = _require_inherited_descriptor(
            leader_raw, "service leader descriptor"
        )
        os.set_inheritable(leader_fd, False)
    except OSError as exc:
        raise service.ServiceError("service leader descriptor is unavailable") from exc
    try:
        parent_fd = _require_inherited_descriptor(
            parent_raw, "service parent-lifetime descriptor"
        )
        os.set_inheritable(parent_fd, False)
        os.set_blocking(parent_fd, False)
    except OSError as exc:
        raise service.ServiceError("service parent-lifetime descriptor is unavailable") from exc
    actual = profile_config_digest(config)
    if not hmac.compare_digest(actual, expected):
        raise service.ServiceError("service profile changed after supervisor validation")
    return config


@contextmanager
def exclusive_service_lock(config: dict, registry_path: Path):
    del registry_path  # Registry copies must not create distinct locks for one tenant state root.
    lock_path = core.ensure_state_root(Path(config["paths"]["state_root"])) / ".service-operation.lock"
    try:
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
    except OSError as exc:
        raise service.ServiceError("service state-root operation lock is unavailable") from exc
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise service.ServiceError("service or maintenance operation is already active") from exc
        yield
    finally:
        os.close(descriptor)


def forbidden_registry_roots(config: dict) -> tuple[Path, ...]:
    """Locations that can never supply enrollment authority.

    The registry is service configuration. It must not live inside this source
    tree, the reviewed target checkout, or the mutable state/proof roots (which
    also hold the reviewer inboxes), otherwise repository-controlled or
    engine-written bytes could enroll targets.
    """
    paths = config["paths"]
    roots = [ROOT]
    for key in ("blocks_checkout", "state_root", "proof_root"):
        roots.append(Path(paths[key]))
    return tuple(roots)


def _forbidden_identities(forbidden_roots: Iterable[Path]) -> set[tuple[int, int]]:
    identities = set()
    for root in forbidden_roots:
        try:
            metadata = os.stat(root)
        except OSError:
            continue  # a root that does not exist cannot contain the registry
        identities.add((metadata.st_dev, metadata.st_ino))
    return identities


def _open_registry_descriptor(
    parent: Path, name: str, forbidden_roots: Iterable[Path], parent_identity: tuple[int, int]
) -> int:
    """Walk the canonical parent with held directory descriptors and open the leaf.

    Every component is opened relative to the previously validated directory with
    O_NOFOLLOW, so an ancestor swapped for a symlink after canonicalization fails
    instead of being followed. Forbidden roots are compared by device/inode on
    each held directory. The held parent must be the very directory that was
    canonicalized (same device/inode), so a real directory renamed into place
    after canonicalization is refused as well; the parent is then validated on
    its descriptor before the original leaf name is opened relative to it with
    O_NOFOLLOW. The leaf is never canonicalized, so a leaf swapped for a symlink
    is refused too.
    """
    forbidden = _forbidden_identities(forbidden_roots)
    walked: list[tuple[int, int]] = []
    parts = parent.parts
    if not parts or name in ("", ".", "..") or "/" in name:
        raise service.ServiceError("service enrollment registry must be a file below the filesystem root")
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    try:
        held = os.open(parts[0], directory_flags)
    except OSError as exc:
        raise service.ServiceError("service enrollment registry path is unavailable") from exc
    try:
        root_metadata = os.fstat(held)
        walked.append((root_metadata.st_dev, root_metadata.st_ino))
        for component in parts[1:]:
            try:
                following = os.open(component, directory_flags, dir_fd=held)
            except OSError as exc:
                raise service.ServiceError(
                    "service enrollment registry path component is unavailable or is a symlink"
                ) from exc
            os.close(held)
            held = following
            metadata = os.fstat(held)
            walked.append((metadata.st_dev, metadata.st_ino))
            if (metadata.st_dev, metadata.st_ino) in forbidden:
                raise service.ServiceError(
                    "service enrollment registry must be service-owned, outside source, checkout, state and proof roots"
                )
        # The forbidden identities above were a snapshot taken before the walk. A
        # root created, renamed or re-pointed meanwhile must not now resolve to any
        # directory that was walked (the parent included), so re-stat every root
        # path against the walked identities before the leaf is opened.
        if _forbidden_identities(forbidden_roots) & set(walked):
            raise service.ServiceError(
                "service enrollment registry path entered a forbidden root during validation"
            )
        parent_metadata = os.fstat(held)
        if (parent_metadata.st_dev, parent_metadata.st_ino) != parent_identity:
            raise service.ServiceError(
                "service enrollment registry parent changed after canonicalization"
            )
        if (
            not stat.S_ISDIR(parent_metadata.st_mode)
            or parent_metadata.st_uid != os.getuid()
            or parent_metadata.st_mode & 0o022
        ):
            raise service.ServiceError(
                "service enrollment registry parent must be a same-user directory that is not group/world writable"
            )
        try:
            # O_NONBLOCK keeps a same-user FIFO at this path from blocking startup
            # forever; the descriptor is then required to be a regular file.
            return os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, dir_fd=held)
        except OSError as exc:
            raise service.ServiceError("service enrollment registry is unavailable or is a symlink") from exc
    finally:
        os.close(held)


def _registry_bytes(
    parent: Path, name: str, forbidden_roots: Iterable[Path], parent_identity: tuple[int, int] | None = None
) -> bytes:
    """Open once via held directories, validate the descriptor, read from it.

    Every ownership/mode/size check runs on the opened descriptor, so a same-user
    writer cannot swap the path between validation and the read.
    """
    if parent_identity is None:
        try:
            metadata = os.stat(parent)
        except OSError as exc:
            raise service.ServiceError("service enrollment registry is unavailable") from exc
        parent_identity = (metadata.st_dev, metadata.st_ino)
    descriptor = _open_registry_descriptor(parent, name, forbidden_roots, parent_identity)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid():
            raise service.ServiceError("service enrollment registry must be a same-user regular file")
        if stat.S_IMODE(metadata.st_mode) != REGISTRY_MODE:
            raise service.ServiceError("service enrollment registry must have mode 0600 exactly")
        if metadata.st_nlink != 1:
            raise service.ServiceError("service enrollment registry must have exactly one link")
        if metadata.st_size > admission.MAX_REGISTRY_BYTES:
            raise service.ServiceError("service enrollment registry exceeds its bounded size")
        chunks = []
        remaining = admission.MAX_REGISTRY_BYTES + 1
        while remaining > 0:
            try:
                chunk = os.read(descriptor, remaining)
            except OSError as exc:
                raise service.ServiceError("service enrollment registry could not be read") from exc
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        if len(raw) > admission.MAX_REGISTRY_BYTES:
            raise service.ServiceError("service enrollment registry exceeds its bounded size")
        return raw
    finally:
        os.close(descriptor)


def read_service_registry(path: Path, forbidden_roots: Iterable[Path]) -> admission.Registry:
    if not isinstance(path, Path) or not path.is_absolute():
        raise service.ServiceError("service enrollment registry must be an absolute regular file")
    try:
        # Canonicalize only the ancestors (for example /var -> /private/var); the
        # leaf is opened by its original name with O_NOFOLLOW under the held
        # parent, so it is never followed. Symlink loops raise RuntimeError on
        # older Pythons and OSError on newer ones; both are an unavailable path.
        parent = path.parent.resolve(strict=True)
        # Pin the canonical parent's identity; the descriptor walk must land on
        # exactly this directory, not a real directory renamed into its place.
        metadata = os.stat(parent)
    except (OSError, RuntimeError) as exc:
        raise service.ServiceError("service enrollment registry is unavailable") from exc
    raw = _registry_bytes(
        parent, path.name, (ROOT, *tuple(forbidden_roots)), (metadata.st_dev, metadata.st_ino)
    )
    try:
        registry = admission.load_registry(raw)
    except admission.AdmissionError as exc:
        raise service.ServiceError("service enrollment registry failed validation") from exc
    if not registry.enrollments:
        raise service.ServiceError("service enrollment registry enrolls no repository")
    return registry


def registry_provider(path: Path, config: dict):
    """Re-read and re-validate the service-owned registry on every use.

    Promotion or revocation written to the registry is observed by the next
    delivery or worker tick; a registry that stops validating fails closed.
    """
    roots = forbidden_registry_roots(config)

    def provide() -> admission.Registry:
        registry = read_service_registry(path, roots)
        # App/installation/repository and the reviewer actors must all be the
        # registry's enrollment; the profile cannot supply any of them.
        service.require_profile_enrolled(config, registry)
        return registry

    return provide


class ServiceStopped(service.ServiceError):
    """The worker hit an operational failure and stopped the whole service."""


def serve(profile_path: Path, registry_path: Path) -> None:
    """Hold a restrictive process umask for the complete threaded lifecycle."""
    config = load_supervised_profile(profile_path)
    profiles.require_enabled(config)
    registry_provider(registry_path, config)()
    previous_umask = os.umask(0o077)
    try:
        with exclusive_service_lock(config, registry_path):
            _serve_with_restrictive_umask(config, registry_path)
    finally:
        os.umask(previous_umask)


def _serve_with_restrictive_umask(config: dict, registry_path: Path) -> None:
    profiles.require_enabled(config)
    provide_registry = registry_provider(registry_path, config)
    provide_registry()
    webhook_secret = userland.read_inherited_value(
        config["credentials"]["webhook_secret_fd_env"], "GitHub webhook secret"
    )
    client = userland.build_client(config)
    notifier = userland.OpenClawNotifier(config)
    stop = threading.Event()
    parent_lost = threading.Event()
    session_drained = threading.Event()
    drain_lock = threading.Lock()
    failure: list[BaseException] = []
    server = None
    parent_raw = os.environ.get(PARENT_LIFETIME_FD_ENV)
    parent_fd = None if parent_raw is None else int(parent_raw)
    previous_handlers: dict[int, Any] = {}

    def request_stop(_signum: int | None = None, _frame: Any = None) -> None:
        stop.set()
        current = server
        if current is not None:
            # BaseServer.shutdown waits for serve_forever and deadlocks if it
            # runs on that same thread. Signal handlers share the serving thread.
            threading.Thread(
                target=current.shutdown,
                name="review-conductor-shutdown",
                daemon=True,
            ).start()

    def drain_once() -> None:
        with drain_lock:
            if session_drained.is_set():
                return
            session_drained.set()
        drain_owned_session()

    def lose_parent() -> None:
        parent_lost.set()
        request_stop()
        current = server
        if current is not None:
            with contextlib.suppress(OSError):
                current.server_close()
        # Drain here, not only in finally: a requested stop may already be
        # blocked in worker.join(), so finally cannot take the parent-loss path.
        drain_once()

    def watch_parent() -> None:
        if parent_fd is None:
            return
        # Stop requested is not shutdown complete. SIGTERM/SIGHUP or worker
        # failure can enter an unbounded worker.join while the supervisor later
        # disappears; exiting on stop would leave parent_lost false and orphan
        # the still-owned session.
        while not parent_lost.is_set():
            try:
                if parent_lifetime_lost(parent_fd):
                    lose_parent()
                    return
            except service.ServiceError as exc:
                failure.append(exc)
                lose_parent()
                return
            time.sleep(0.05)

    worker = threading.Thread(
        target=lambda: _worker_loop(
            config, provide_registry, client, notifier, stop, failure, lambda: server, parent_lost
        ),
        name="review-conductor-service",
        daemon=False,
    )
    try:
        # Bind ingress before any worker exists so a failed startup cannot leave a
        # detached worker draining actions, publishing checks or notifying.
        if parent_fd is not None:
            for signum in STOP_SIGNALS:
                previous_handlers[signum] = signal.signal(signum, request_stop)
        handler = service.build_service_http_handler(
            config,
            secret=webhook_secret,
            registry=provide_registry,
            read_policy=client.read_policy,
        )
        server = runtime.BoundedHTTPServer(
            (config["ingress"]["bind_host"], config["ingress"]["bind_port"]),
            handler,
            request_timeout_seconds=config["ingress"]["request_timeout_seconds"],
        )
        if parent_fd is not None:
            threading.Thread(
                target=watch_parent,
                name="review-conductor-parent-lifetime",
                daemon=True,
            ).start()
        worker.start()
        if not stop.is_set() and not parent_lost.is_set():
            server.serve_forever(poll_interval=0.5)
    finally:
        stop.set()
        if server is not None:
            server.server_close()
        if parent_lost.is_set():
            # Parent-loss drain is independent of the worker. An in-flight adapter
            # or TERM-resistant descendant must not delay the session reap after
            # the original supervisor is already gone.
            drain_once()
        elif worker.is_alive():
            # A tick may be inside a bounded external adapter operation far longer
            # than the polling interval. Never return while that worker still owns
            # a claim or subprocess; its adapter timeout remains the upper bound.
            worker.join()
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
        webhook_secret = ""
    if failure:
        # The worker stopped the service; surface its operational failure instead of
        # letting ingress keep accepting deliveries that nothing will act on.
        raise ServiceStopped("worker failed; service stopped") from failure[0]


def _worker_loop(config, provide_registry, client, notifier, stop, failure, get_server, parent_lost=None) -> None:
    while not stop.is_set() and (parent_lost is None or not parent_lost.is_set()):
        try:
            service.run_service_tick(
                config, provide_registry, client, notifier, dry_run=False
            )
        except core.ContractError:
            # Fail-closed admission/policy conditions are retried on the next tick.
            pass
        except BaseException as exc:  # sqlite3.Error, OSError, or anything unexpected
            failure.append(exc)
            stop.set()
            server = get_server()
            if server is not None:
                server.shutdown()
            return
        if parent_lost is not None and parent_lost.is_set():
            return
        if stop.wait(config["worker"]["tick_seconds"]):
            return


def run_maintenance(
    profile_path: Path, registry_path: Path, command: str, pr_number: int, *,
    apply: bool, channel: str | None = None, disposition: str | None = None,
    confirmation: str | None = None,
) -> dict:
    config = userland.load_config(profile_path)
    profiles.require_enabled(config)
    registry_provider(registry_path, config)()
    with exclusive_service_lock(config, registry_path):
        return _run_maintenance_unlocked(
            config, registry_path, command, pr_number, apply=apply,
            channel=channel, disposition=disposition, confirmation=confirmation,
        )


def _run_maintenance_unlocked(
    config: dict,
    registry_path: Path,
    command: str,
    pr_number: int,
    *,
    apply: bool,
    channel: str | None = None,
    disposition: str | None = None,
    confirmation: str | None = None,
) -> dict:
    """Run a supported state recovery only under current standalone authority."""
    profiles.require_enabled(config)
    provide_registry = registry_provider(registry_path, config)
    # Match the worker's whole-profile opening gate, then re-check the target
    # binding inside the mutation transaction immediately before state changes.
    service.require_current_bindings(config, provide_registry)

    def require_target(connection) -> None:
        service.require_current_binding(
            connection, config, provide_registry, pr_number
        )

    if command == "retry-openclaw":
        return userland.retry_failed_openclaw(
            config,
            pr_number,
            apply=apply,
            authority_guard=require_target,
        )
    if command == "reconcile-notification":
        return userland.reconcile_uncertain_notification(
            config,
            pr_number,
            channel,
            disposition,
            confirmation,
            apply=apply,
            authority_guard=require_target,
        )
    raise service.ServiceError("unsupported standalone maintenance operation")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--registry", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument(
        "command",
        nargs="?",
        default="serve",
        choices=("serve", "retry-openclaw", "reconcile-notification"),
    )
    parser.add_argument("--pr", type=int)
    parser.add_argument(
        "--channel", choices=("openclaw_context", "discord", "signal")
    )
    parser.add_argument("--disposition", choices=("sent", "retry"))
    parser.add_argument("--confirm")
    args = parser.parse_args(argv)
    if args.command == "serve":
        if args.dry_run:
            parser.error("serve requires --apply")
        if any(
            value is not None
            for value in (args.pr, args.channel, args.disposition, args.confirm)
        ):
            parser.error("serve does not accept maintenance arguments")
    elif args.pr is None:
        parser.error(f"{args.command} requires --pr")
    elif args.command == "retry-openclaw" and any(
        value is not None for value in (args.channel, args.disposition, args.confirm)
    ):
        parser.error("retry-openclaw does not accept notification arguments")
    elif args.command == "reconcile-notification" and any(
        value is None for value in (args.channel, args.disposition, args.confirm)
    ):
        parser.error(
            "reconcile-notification requires --channel, --disposition and --confirm"
        )
    try:
        if args.command == "serve":
            serve(args.profile, args.registry)
        else:
            result = run_maintenance(
                args.profile,
                args.registry,
                args.command,
                args.pr,
                apply=args.apply,
                channel=args.channel,
                disposition=args.disposition,
                confirmation=args.confirm,
            )
            print(json.dumps(result, indent=2, sort_keys=True))
    except (core.ContractError, OSError, sqlite3.Error) as exc:
        print(f"review-conductor-service: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
