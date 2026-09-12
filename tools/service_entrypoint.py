#!/usr/bin/env python3
"""Inactive standalone service entrypoint; activation requires external state."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import stat
import sys
import threading
from typing import Iterable

import review_conductor as core
import review_conductor_profiles as profiles
import review_conductor_runtime as runtime
import review_conductor_userland as userland
import service_runtime as service
import trusted_admission as admission

ROOT = Path(__file__).resolve().parents[1]
REGISTRY_MODE = 0o600


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


def _open_registry_descriptor(path: Path, forbidden_roots: Iterable[Path]) -> int:
    """Walk the canonical path with held directory descriptors and open the leaf.

    Every component is opened relative to the previously validated directory with
    O_NOFOLLOW, so an ancestor swapped for a symlink after canonicalization fails
    instead of being followed. Forbidden roots are compared by device/inode on
    each held directory, and the parent is validated on its descriptor before
    the leaf is opened relative to it.
    """
    forbidden = _forbidden_identities(forbidden_roots)
    parts = path.parts
    if len(parts) < 2:
        raise service.ServiceError("service enrollment registry must be a file below the filesystem root")
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    try:
        held = os.open(parts[0], directory_flags)
    except OSError as exc:
        raise service.ServiceError("service enrollment registry path is unavailable") from exc
    try:
        for name in parts[1:-1]:
            try:
                following = os.open(name, directory_flags, dir_fd=held)
            except OSError as exc:
                raise service.ServiceError(
                    "service enrollment registry path component is unavailable or is a symlink"
                ) from exc
            os.close(held)
            held = following
            metadata = os.fstat(held)
            if (metadata.st_dev, metadata.st_ino) in forbidden:
                raise service.ServiceError(
                    "service enrollment registry must be service-owned, outside source, checkout, state and proof roots"
                )
        parent = os.fstat(held)
        if (
            not stat.S_ISDIR(parent.st_mode)
            or parent.st_uid != os.getuid()
            or parent.st_mode & 0o022
        ):
            raise service.ServiceError(
                "service enrollment registry parent must be a same-user directory that is not group/world writable"
            )
        try:
            return os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=held)
        except OSError as exc:
            raise service.ServiceError("service enrollment registry is unavailable or is a symlink") from exc
    finally:
        os.close(held)


def _registry_bytes(path: Path, forbidden_roots: Iterable[Path]) -> bytes:
    """Open once via held directories, validate the descriptor, read from it.

    Every ownership/mode/size check runs on the opened descriptor, so a same-user
    writer cannot swap the path between validation and the read.
    """
    descriptor = _open_registry_descriptor(path, forbidden_roots)
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


def read_service_registry(path: Path, forbidden_roots: Iterable[Path] = ()) -> admission.Registry:
    if not isinstance(path, Path) or not path.is_absolute() or path.is_symlink():
        raise service.ServiceError("service enrollment registry must be an absolute regular file")
    try:
        # Canonicalize operator-supplied ancestor symlinks (for example
        # /var -> /private/var) once; the descriptor walk below refuses any
        # symlink that appears afterwards and checks forbidden roots by identity.
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise service.ServiceError("service enrollment registry is unavailable") from exc
    raw = _registry_bytes(resolved, tuple(forbidden_roots))
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
    config = userland.load_config(profile_path)
    profiles.require_enabled(config)
    provide_registry = registry_provider(registry_path, config)
    provide_registry()
    webhook_secret = userland.read_inherited_value(
        config["credentials"]["webhook_secret_fd_env"], "GitHub webhook secret"
    )
    client = userland.build_client(config)
    notifier = userland.OpenClawNotifier(config)
    stop = threading.Event()
    failure: list[BaseException] = []
    server = None
    worker = threading.Thread(
        target=lambda: _worker_loop(config, provide_registry, client, notifier, stop, failure, lambda: server),
        name="review-conductor-service",
        daemon=True,
    )
    try:
        # Bind ingress before any worker exists so a failed startup cannot leave a
        # detached worker draining actions, publishing checks or notifying.
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
        worker.start()
        server.serve_forever(poll_interval=0.5)
    finally:
        stop.set()
        if server is not None:
            server.server_close()
        if worker.is_alive():
            worker.join(timeout=config["worker"]["tick_seconds"] + 1)
        webhook_secret = ""
    if failure:
        # The worker stopped the service; surface its operational failure instead of
        # letting ingress keep accepting deliveries that nothing will act on.
        raise ServiceStopped("worker failed; service stopped") from failure[0]


def _worker_loop(config, provide_registry, client, notifier, stop, failure, get_server) -> None:
    while not stop.is_set():
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
        if stop.wait(config["worker"]["tick_seconds"]):
            return


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--apply", action="store_true", required=True)
    args = parser.parse_args(argv)
    try:
        serve(args.profile, args.registry)
    except (core.ContractError, OSError) as exc:
        print(f"review-conductor-service: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
