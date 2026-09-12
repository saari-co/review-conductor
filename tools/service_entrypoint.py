#!/usr/bin/env python3
"""Inactive standalone service entrypoint; activation requires external state."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import stat
import sys
import threading

import review_conductor as core
import review_conductor_profiles as profiles
import review_conductor_runtime as runtime
import review_conductor_userland as userland
import service_runtime as service
import trusted_admission as admission


def read_service_registry(path: Path) -> admission.Registry:
    if not path.is_absolute() or path.is_symlink():
        raise service.ServiceError("service enrollment registry must be an absolute regular file")
    try:
        metadata = path.stat()
    except OSError as exc:
        raise service.ServiceError("service enrollment registry is unavailable") from exc
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid():
        raise service.ServiceError("service enrollment registry must be a same-user regular file")
    if metadata.st_mode & 0o077:
        raise service.ServiceError("service enrollment registry must not be group/world accessible")
    if metadata.st_size > admission.MAX_REGISTRY_BYTES:
        raise service.ServiceError("service enrollment registry exceeds its bounded size")
    try:
        registry = admission.load_registry(path.read_bytes())
    except (OSError, admission.AdmissionError) as exc:
        raise service.ServiceError("service enrollment registry failed validation") from exc
    if not registry.enrollments:
        raise service.ServiceError("service enrollment registry enrolls no repository")
    return registry


def serve(profile_path: Path, registry_path: Path) -> None:
    config = userland.load_config(profile_path)
    profiles.require_enabled(config)
    registry = read_service_registry(registry_path)
    app = config["github_app"]
    try:
        registry.lookup(
            app["repository"], app["repository_id"], app["app_id"], app["installation_id"]
        )
    except admission.AdmissionError as exc:
        raise service.ServiceError(
            "runtime profile is not enrolled in the service registry"
        ) from exc
    webhook_secret = userland.read_inherited_value(
        config["credentials"]["webhook_secret_fd_env"], "GitHub webhook secret"
    )
    client = userland.build_client(config)
    notifier = userland.OpenClawNotifier(config)
    stop = threading.Event()

    def worker_loop() -> None:
        while not stop.is_set():
            try:
                service.run_service_tick(
                    config, registry, client, notifier, dry_run=False
                )
            except core.ContractError:
                pass
            if stop.wait(config["worker"]["tick_seconds"]):
                return

    worker = threading.Thread(
        target=worker_loop, name="review-conductor-service", daemon=True
    )
    worker.start()
    handler = service.build_service_http_handler(
        config,
        secret=webhook_secret,
        registry=registry,
        read_policy=client.read_policy,
    )
    server = runtime.BoundedHTTPServer(
        (config["ingress"]["bind_host"], config["ingress"]["bind_port"]),
        handler,
        request_timeout_seconds=config["ingress"]["request_timeout_seconds"],
    )
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        stop.set()
        server.server_close()
        worker.join(timeout=config["worker"]["tick_seconds"] + 1)
        webhook_secret = ""


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
