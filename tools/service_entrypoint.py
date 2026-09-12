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


def read_service_registry(path: Path, forbidden_roots: Iterable[Path] = ()) -> admission.Registry:
    if not isinstance(path, Path) or not path.is_absolute() or path.is_symlink():
        raise service.ServiceError("service enrollment registry must be an absolute regular file")
    try:
        resolved = path.resolve(strict=True)
        metadata = path.stat()
    except OSError as exc:
        raise service.ServiceError("service enrollment registry is unavailable") from exc
    for root in forbidden_roots:
        try:
            root = root.resolve()
        except OSError:
            pass
        if resolved == root or resolved.is_relative_to(root):
            raise service.ServiceError(
                "service enrollment registry must be service-owned, outside source, checkout, state and proof roots"
            )
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid():
        raise service.ServiceError("service enrollment registry must be a same-user regular file")
    if stat.S_IMODE(metadata.st_mode) != REGISTRY_MODE:
        raise service.ServiceError("service enrollment registry must have mode 0600 exactly")
    if metadata.st_size > admission.MAX_REGISTRY_BYTES:
        raise service.ServiceError("service enrollment registry exceeds its bounded size")
    try:
        registry = admission.load_registry(path.read_bytes())
    except (OSError, admission.AdmissionError) as exc:
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
    app = config["github_app"]

    def provide() -> admission.Registry:
        registry = read_service_registry(path, roots)
        try:
            registry.lookup(
                app["repository"], app["repository_id"], app["app_id"], app["installation_id"]
            )
        except admission.AdmissionError as exc:
            raise service.ServiceError(
                "runtime profile is not enrolled in the service registry"
            ) from exc
        return registry

    return provide


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
    worker = threading.Thread(
        target=lambda: _worker_loop(config, provide_registry, client, notifier, stop),
        name="review-conductor-service",
        daemon=True,
    )
    server = None
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


def _worker_loop(config, provide_registry, client, notifier, stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            service.run_service_tick(
                config, provide_registry, client, notifier, dry_run=False
            )
        except core.ContractError:
            pass
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
