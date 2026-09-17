#!/usr/bin/env python3
"""Isolated contract/replay proof for shared Review Conductor adapters."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
import review_conductor as core  # noqa: E402
import review_conductor_runtime as runtime  # noqa: E402
import review_conductor_userland as userland  # noqa: E402


CORE_CONFIG = ROOT / "contracts/review-conductor/dinkuskit-blocks.json"
SECRET = "fixture-webhook-secret-not-for-live-use"
BASE = "1" * 40


def write_json(path: Path, value: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def fixture_config(root: Path) -> tuple[Path, dict[str, Any]]:
    home = root / "home"
    home.mkdir(parents=True)
    config = userland.load_config(
        userland.DEFAULT_CONFIG,
        home=home,
        source_root=ROOT,
    )
    for directory in (
        Path(config["paths"]["state_root"]),
        Path(config["paths"]["proof_root"]),
        Path(config["paths"]["blocks_checkout"]),
        Path(config["paths"]["action_wake"]).parent,
        Path(config["paths"]["projection_wake"]).parent,
        Path(config["spark"]["terminal_inbox"]),
        Path(config["clawsweeper_bridge"]["terminal_inbox"]),
    ):
        directory.mkdir(parents=True, exist_ok=True)
    path = write_json(root / "userland-runtime.json", config)
    return path, config


def pr_payload(
    pr: int,
    head: str,
    action: str = "opened",
    *,
    updated_at: str = "2026-08-28T03:00:00Z",
    merged: bool = False,
) -> dict[str, Any]:
    return {
        "action": action,
        "repository": {"id": 1306882611, "full_name": "dinkuskit/blocks"},
        "pull_request": {
            "number": pr,
            "base": {"ref": "main", "sha": BASE},
            "head": {"sha": head},
            "user": {"login": "alice"},
            "updated_at": updated_at,
            "merged": merged,
        },
    }


def ci_payload(
    pr: int,
    head: str,
    conclusion: str,
    run_id: int,
    *,
    created_at: str = "2026-08-28T03:10:00Z",
) -> dict[str, Any]:
    return {
        "action": "completed",
        "repository": {"id": 1306882611, "full_name": "dinkuskit/blocks"},
        "workflow": {"name": "CI", "path": ".github/workflows/ci.yml"},
        "workflow_run": {
            "id": run_id,
            "event": "pull_request",
            "status": "completed",
            "conclusion": conclusion,
            "head_sha": head,
            "created_at": created_at,
            "pull_requests": [
                {"number": pr, "base": {"ref": "main", "sha": BASE}, "head": {"sha": head}}
            ],
        },
    }


def clawsweeper_workflow_payload(run_id: int) -> dict[str, Any]:
    return {
        "action": "completed",
        "repository": {"id": 1306882611, "full_name": "dinkuskit/blocks"},
        "workflow": {
            "name": "ClawSweeper native canary",
            "path": ".github/workflows/clawsweeper-native-canary.yml",
        },
        "workflow_run": {
            "id": run_id,
            "event": "workflow_dispatch",
            "status": "completed",
            "conclusion": "success",
            "head_sha": BASE,
            "created_at": "2026-08-28T03:30:00Z",
            "pull_requests": [],
        },
    }


def signed_headers(event_type: str, delivery_id: str, body: bytes, secret: str = SECRET) -> dict[str, str]:
    signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return {
        "content-type": "application/json",
        "x-github-event": event_type,
        "x-github-delivery": delivery_id,
        "x-hub-signature-256": signature,
    }


def ingress(
    config: dict[str, Any], event_type: str, delivery_id: str, payload: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    body = core.canonical_json(payload).encode()
    return runtime.handle_webhook_request(
        config,
        method="POST",
        path="/github/webhook",
        headers=signed_headers(event_type, delivery_id, body),
        body=body,
        secret=SECRET,
    )


def current(config: dict[str, Any], pr: int) -> sqlite3.Row:
    connection = core.open_database(Path(config["paths"]["state_root"]))
    try:
        row = core.current_head(connection, "dinkuskit/blocks", pr)
        assert row is not None
        return row
    finally:
        connection.close()


def actions(config: dict[str, Any], pr: int, kind: str) -> list[sqlite3.Row]:
    connection = core.open_database(Path(config["paths"]["state_root"]))
    try:
        return connection.execute(
            "SELECT * FROM actions WHERE pr_number = ? AND kind = ? ORDER BY created_at, action_id",
            (pr, kind),
        ).fetchall()
    finally:
        connection.close()


def proof(root: Path, name: str) -> tuple[Path, str]:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"immutable fixture proof for {name}\n", encoding="utf-8")
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def openclaw_artifact(
    root: Path,
    config: dict[str, Any],
    action: sqlite3.Row,
    *,
    status: str = "completed",
    clean: bool = True,
    findings: int = 0,
    head_override: str | None = None,
) -> Path:
    payload = json.loads(action["payload_json"])
    proof_path, proof_digest = proof(Path(config["paths"]["proof_root"]), f"openclaw/{action['action_id']}/PROOF.md")
    artifact = {
        "schema": runtime.OPENCLAW_ARTIFACT_SCHEMA,
        "request_id": payload["queue_request_id"],
        "operator_id": payload["operator_id"],
        "status": status,
        "repository": payload["repository"],
        "pr_number": payload["pr_number"],
        "base_sha": payload["base_sha"],
        "head_sha": head_override or payload["head_sha"],
        "review_epoch": payload["review_epoch"],
        "review_clean": clean,
        "review_finding_count": findings,
        "reviewer_actor": "spark-openclaw",
        "proof_ref": str(proof_path),
        "proof_sha256": proof_digest,
    }
    return write_json(root / f"openclaw-{action['action_id']}-{status}.json", artifact)


def clawsweeper_artifact(
    root: Path,
    config: dict[str, Any],
    action: sqlite3.Row,
    run_id: int,
    *,
    verdict: str = "clean",
    findings: int = 0,
    head_override: str | None = None,
) -> Path:
    payload = json.loads(action["payload_json"])
    proof_path, proof_digest = proof(Path(config["paths"]["proof_root"]), f"clawsweeper/{run_id}/PROOF.md")
    return write_json(
        root / f"clawsweeper-{run_id}-{verdict}.json",
        {
            "schema": runtime.CLAWSWEEPER_ARTIFACT_SCHEMA,
            "workflow_run_id": run_id,
            "repository": payload["repository"],
            "pr_number": payload["pr_number"],
            "base_sha": payload["base_sha"],
            "head_sha": head_override or payload["head_sha"],
            "review_epoch": payload["review_epoch"],
            "verdict": verdict,
            "finding_count": findings,
            "reviewer_actor": "clawsweeper",
            "proof_ref": str(proof_path),
            "proof_sha256": proof_digest,
        },
    )


class FakeGitHub:
    def __init__(self, *, label_present: bool = False) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.next_check_id = 500
        self.label_present = label_present

    def create_check(self, name: str, head: str, external_id: str, state: str, **kwargs: Any) -> int:
        self.calls.append(("create_check", name, head, external_id, state))
        self.next_check_id += 1
        return self.next_check_id

    def update_check(self, check_id: int, name: str, head: str, external_id: str, state: str, **kwargs: Any) -> None:
        self.calls.append(("update_check", check_id, name, head, external_id, state))

    def add_ready_label(self, pr: int) -> None:
        self.calls.append(("add_label", pr, runtime.READY_LABEL))
        self.label_present = True

    def remove_ready_label(self, pr: int) -> None:
        self.calls.append(("remove_label", pr, runtime.READY_LABEL))
        self.label_present = False

    def dispatch_clawsweeper(self, **kwargs: Any) -> None:
        self.calls.append(("dispatch_clawsweeper", kwargs))


class FailAfterCheckCreate(FakeGitHub):
    def create_check(self, name: str, head: str, external_id: str, state: str, **kwargs: Any) -> int:
        super().create_check(name, head, external_id, state, **kwargs)
        raise RuntimeError("fixture transport outcome is uncertain")


class CompetingHandoffGitHub(FakeGitHub):
    def __init__(self, config: dict[str, Any], *, label_present: bool) -> None:
        super().__init__(label_present=label_present)
        self.config = config
        self.cleanup_action_statuses: list[str] = []
        self.competing_dispatch_errors: list[str] = []
        self.observe_handoff = True

    def remove_ready_label(self, pr: int) -> None:
        if not self.observe_handoff:
            super().remove_ready_label(pr)
            return
        connection = core.open_database(Path(self.config["paths"]["state_root"]))
        try:
            action = connection.execute(
                "SELECT * FROM actions WHERE pr_number = ? AND kind = 'clawsweeper.dispatch' ORDER BY created_at DESC LIMIT 1",
                (pr,),
            ).fetchone()
        finally:
            connection.close()
        if action is not None:
            self.observe_handoff = False
            self.cleanup_action_statuses.append(action["status"])
            try:
                runtime.dispatch_clawsweeper_action(self.config, action["action_id"], self)
            except RuntimeError as exc:
                self.competing_dispatch_errors.append(str(exc))
            else:
                raise AssertionError("a competing worker must not claim the pre-dispatch cleanup")
        super().remove_ready_label(pr)


def test_http_ingress_limits_signature_dedupe_and_allowlists(root: Path) -> None:
    _, config = fixture_config(root / "http")
    head = "2" * 40
    body = core.canonical_json(pr_payload(20, head)).encode()
    headers = signed_headers("pull_request", "delivery-http-1", body)
    status, receipt = runtime.handle_webhook_request(
        config, method="POST", path="/github/webhook", headers=headers, body=body, secret=SECRET
    )
    assert status == 202 and receipt["result"] == "accepted"
    status, duplicate = runtime.handle_webhook_request(
        config, method="POST", path="/github/webhook", headers=headers, body=body, secret=SECRET
    )
    assert status == 202 and duplicate["result"] == "duplicate_delivery"

    bad_headers = dict(headers)
    bad_headers["x-hub-signature-256"] = "sha256=" + "0" * 64
    status, rejected = runtime.handle_webhook_request(
        config, method="POST", path="/github/webhook", headers=bad_headers, body=body, secret=SECRET
    )
    assert status == 400 and rejected == {"ok": False, "reason": "request_rejected"}
    assert SECRET not in json.dumps(rejected)
    status, _ = runtime.handle_webhook_request(
        config, method="GET", path="/github/webhook", headers=headers, body=b"", secret=SECRET
    )
    assert status == 405
    status, _ = runtime.handle_webhook_request(
        config, method="POST", path="/wrong", headers=headers, body=body, secret=SECRET
    )
    assert status == 404
    wrong_type = dict(headers)
    wrong_type["content-type"] = "text/plain"
    status, _ = runtime.handle_webhook_request(
        config, method="POST", path="/github/webhook", headers=wrong_type, body=body, secret=SECRET
    )
    assert status == 415
    status, _ = runtime.handle_webhook_request(
        config,
        method="POST",
        path="/github/webhook",
        headers=headers,
        body=b"x" * (config["ingress"]["max_body_bytes"] + 1),
        secret=SECRET,
    )
    assert status == 413
    malformed = b"{"
    status, _ = runtime.handle_webhook_request(
        config,
        method="POST",
        path="/github/webhook",
        headers=signed_headers("pull_request", "malformed", malformed),
        body=malformed,
        secret=SECRET,
    )
    assert status == 400
    unsupported = pr_payload(21, "3" * 40, action="edited")
    status, _ = ingress(config, "pull_request", "unsupported-action", unsupported)
    assert status == 400
    wrong_repo = pr_payload(21, "3" * 40)
    wrong_repo["repository"]["id"] = 999
    status, _ = ingress(config, "pull_request", "wrong-repo", wrong_repo)
    assert status == 400
    wrong_workflow = ci_payload(20, head, "success", 2001)
    wrong_workflow["workflow"]["name"] = "Untrusted"
    status, _ = ingress(config, "workflow_run", "wrong-workflow", wrong_workflow)
    assert status == 400
    assert Path(config["paths"]["action_wake"]).is_file()
    assert Path(config["paths"]["projection_wake"]).is_file()



def test_authentication_precedes_parser_state_and_header_deadline(root: Path) -> None:
    _, config = fixture_config(root / "auth-order")
    absent_state = root / "auth-order" / "absent-state"
    body = b"{"
    original_loads = core.json.loads
    core.json.loads = lambda _value: (_ for _ in ()).throw(
        AssertionError("parser ran before HMAC verification")
    )
    try:
        try:
            core.ingest_github_delivery(
                config_path=CORE_CONFIG,
                state_root=absent_state,
                event_type="pull_request",
                delivery_id="auth-order-invalid",
                signature="sha256=" + "0" * 64,
                body=body,
                secret=SECRET,
            )
        except core.ContractError:
            pass
        else:
            raise AssertionError("invalid signature must fail closed")
    finally:
        core.json.loads = original_loads
    assert not absent_state.exists()

    try:
        core.ingest_github_delivery(
            config_path=CORE_CONFIG,
            state_root=absent_state,
            event_type="pull_request",
            delivery_id="auth-order-malformed",
            signature=signed_headers("pull_request", "unused", body)[
                "x-hub-signature-256"
            ],
            body=body,
            secret=SECRET,
        )
    except core.ContractError:
        pass
    else:
        raise AssertionError("authenticated malformed JSON must fail closed")
    assert not absent_state.exists()

    server = runtime.BoundedHTTPServer(
        ("127.0.0.1", 0),
        runtime.build_http_handler(config, SECRET),
        request_timeout_seconds=config["ingress"]["request_timeout_seconds"],
    )
    client = socket.create_connection(server.server_address, timeout=2)
    accepted, _address = server.get_request()
    try:
        assert accepted.gettimeout() == config["ingress"]["request_timeout_seconds"]
    finally:
        accepted.close()
        client.close()
        server.server_close()


def test_abandoned_claim_recovery_is_kind_safe(root: Path) -> None:
    _, config = fixture_config(root / "recovery")
    head = "4" * 40
    assert ingress(config, "pull_request", "recovery-pr", pr_payload(30, head))[0] == 202
    assert ingress(config, "workflow_run", "recovery-ci", ci_payload(30, head, "success", 3001))[0] == 202
    action = actions(config, 30, "openclaw.enqueue")[0]
    connection = core.open_database(Path(config["paths"]["state_root"]))
    try:
        connection.execute(
            "UPDATE actions SET status = 'dispatching', lease_expires_at = '2020-01-01T00:00:00Z' WHERE action_id = ?",
            (action["action_id"],),
        )
        claw_id, claw_key = core.action_identity(
            "clawsweeper.dispatch",
            "dinkuskit/blocks",
            30,
            BASE,
            head,
            core.review_action_suffix(0),
        )
        connection.execute(
            """
            INSERT INTO actions(action_id, idempotency_key, kind, repository, pr_number, base_sha, head_sha,
              review_epoch, status, payload_json, attempts, lease_expires_at, created_at, updated_at)
            VALUES (?, ?, 'clawsweeper.dispatch', 'dinkuskit/blocks', 30, ?, ?, 0,
              'dispatching', '{}', 1, '2020-01-01T00:00:00Z', ?, ?)
            """,
            (
                claw_id,
                claw_key,
                BASE,
                head,
                core.utc_now(),
                core.utc_now(),
            ),
        )
        connection.commit()
    finally:
        connection.close()
    recovered = runtime.recover_abandoned_actions(config)
    states = {item["kind"]: item["status"] for item in recovered}
    assert states == {
        "openclaw.enqueue": "failed",
        "clawsweeper.dispatch": "reconcile_required",
    }
    planned = runtime.drain_actions(config, None, dry_run=True)
    assert planned["github_ci_polled"] is False
    assert planned["actions"] == []
    assert planned["failed_actions"] == [
        {
            "action_id": action["action_id"],
            "pr_number": 30,
            "attempts": action["attempts"],
            "result": "planned",
        }
    ]


def test_abandoned_clawsweeper_preparation_is_safely_retryable(root: Path) -> None:
    _, config = fixture_config(root / "preparation-recovery")
    pr = 31
    head = "5" * 40
    assert ingress(config, "pull_request", "preparation-recovery-pr", pr_payload(pr, head))[0] == 202
    action_id, action_key = core.action_identity(
        "clawsweeper.dispatch",
        "dinkuskit/blocks",
        pr,
        BASE,
        head,
        core.review_action_suffix(0),
    )
    connection = core.open_database(Path(config["paths"]["state_root"]))
    try:
        connection.execute(
            """
            INSERT INTO actions(action_id, idempotency_key, kind, repository, pr_number, base_sha, head_sha,
              review_epoch, status, payload_json, attempts, claim_owner, claimed_at,
              lease_expires_at, created_at, updated_at)
            VALUES (?, ?, 'clawsweeper.dispatch', 'dinkuskit/blocks', ?, ?, ?, 0,
              'preparing', '{}', 0, 'cp1-worker', '2020-01-01T00:00:00Z',
              '2020-01-01T00:00:00Z', ?, ?)
            """,
            (action_id, action_key, pr, BASE, head, core.utc_now(), core.utc_now()),
        )
        connection.commit()
    finally:
        connection.close()
    recovered = runtime.recover_abandoned_actions(config)
    assert recovered == [
        {"action_id": action_id, "kind": "clawsweeper.dispatch", "status": "pending"}
    ]
    assert actions(config, pr, "clawsweeper.dispatch")[0]["status"] == "pending"


def test_epoch_reuse_cannot_revive_superseded_action(root: Path) -> None:
    _, config = fixture_config(root / "epoch-reuse")
    pr = 35
    head_a = "a" * 40
    head_b = "b" * 40
    assert ingress(config, "pull_request", "epoch-a-open", pr_payload(pr, head_a))[0] == 202
    assert ingress(
        config,
        "workflow_run",
        "epoch-a-ci",
        ci_payload(pr, head_a, "success", 3501),
    )[0] == 202
    epoch_zero = actions(config, pr, "openclaw.enqueue")[0]
    assert epoch_zero["review_epoch"] == 0
    connection = core.open_database(Path(config["paths"]["state_root"]))
    try:
        connection.execute(
            "UPDATE actions SET status = 'failed' WHERE action_id = ?",
            (epoch_zero["action_id"],),
        )
        connection.commit()
    finally:
        connection.close()
    assert ingress(
        config,
        "pull_request",
        "epoch-b-sync",
        pr_payload(
            pr,
            head_b,
            action="synchronize",
            updated_at="2026-08-28T03:20:00Z",
        ),
    )[0] == 202
    assert actions(config, pr, "openclaw.enqueue")[0]["status"] == "obsolete"
    assert ingress(
        config,
        "pull_request",
        "epoch-a-return",
        pr_payload(
            pr,
            head_a,
            action="synchronize",
            updated_at="2026-08-28T03:30:00Z",
        ),
    )[0] == 202
    returned = current(config, pr)
    assert returned["head_sha"] == head_a
    assert returned["review_epoch"] == 1
    assert returned["state"] == "ci_running"
    planned = runtime.drain_actions(config, None, dry_run=True)
    assert planned["actions"] == []
    assert ingress(
        config,
        "workflow_run",
        "epoch-a-new-ci",
        ci_payload(
            pr,
            head_a,
            "success",
            3502,
            created_at="2026-08-28T03:31:00Z",
        ),
    )[0] == 202
    epoch_actions = actions(config, pr, "openclaw.enqueue")
    by_epoch = {row["review_epoch"]: row for row in epoch_actions}
    assert set(by_epoch) == {0, 1}
    assert by_epoch[0]["status"] == "obsolete"
    assert by_epoch[0]["action_id"] != by_epoch[1]["action_id"]
    dispatchable = runtime.drain_actions(config, None, dry_run=True)["actions"]
    assert [row["action_id"] for row in dispatchable] == [by_epoch[1]["action_id"]]


def test_github_client_endpoint_and_credential_boundaries(root: Path) -> None:
    _, config = fixture_config(root / "client")
    calls: list[tuple[str, str, dict[str, str], bytes | None]] = []

    def transport(method: str, url: str, headers: dict[str, str], body: bytes | None, _timeout: float) -> tuple[int, bytes]:
        calls.append((method, url, headers, body))
        if url.endswith("/check-runs") and method == "POST":
            return 201, b'{"id":700}'
        if url.endswith("/actions/runs/800/artifacts") and method == "GET":
            return 200, b'{"artifacts":[{"id":801,"name":"dinkuskit-native-review-800-1","expired":false}]}'
        if url.endswith("/actions/artifacts/801/zip") and method == "GET":
            return 200, b"bounded-zip-fixture"
        if url.endswith("/labels"):
            return 200, b'[{"name":"ready-for-human"}]'
        if method == "PATCH":
            return 200, b"{}"
        return 204, b""

    client = runtime.GitHubAppClient(config, "fixture-key-shape", transport=transport)
    client._token = "fixture-installation-token"  # in-memory test seam; never persisted
    client._token_expires = 9_999_999_999
    check = client.create_check("OpenClaw Review Rail", "5" * 40, "review-conductor:x", "queued")
    assert check == 700
    client.update_check(check, "OpenClaw Review Rail", "5" * 40, "review-conductor:x", "success")
    client.add_ready_label(40)
    client.remove_ready_label(40)
    client.dispatch_clawsweeper(pr_number=40, base_sha=BASE, head_sha="5" * 40, publish=True)
    assert client.list_run_artifacts(800)[0]["id"] == 801
    assert client.download_artifact(801) == b"bounded-zip-fixture"
    assert len(calls) == 7
    assert all(call[1].startswith("https://api.github.com/") for call in calls)
    assert all("Authorization" in call[2] for call in calls)
    try:
        client._call("PUT", "/repos/dinkuskit/blocks/pulls/40/merge", {}, expected={200})
    except runtime.GitHubApiError:
        pass
    else:
        raise AssertionError("merge endpoint must be mechanically denied")

    token_calls: list[tuple[str, str, dict[str, str], bytes | None]] = []

    def token_transport(
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None,
        _timeout: float,
    ) -> tuple[int, bytes]:
        token_calls.append((method, url, headers, body))
        if url.endswith("/access_tokens"):
            return 201, b'{"token":"fixture-memory-only","expires_at":"2030-01-01T00:00:00Z"}'
        return 201, b'{"id":701}'

    original_signer = runtime.sign_app_jwt
    runtime.sign_app_jwt = lambda _app_id, _key, _now: "fixture-app-jwt"
    try:
        minted = runtime.GitHubAppClient(
            config,
            "fixture-key-shape",
            transport=token_transport,
            clock=lambda: 1_800_000_000,
        )
        assert minted.create_check(
            "OpenClaw Review Rail", "5" * 40, "review-conductor:y", "queued"
        ) == 701
    finally:
        runtime.sign_app_jwt = original_signer
    token_request = json.loads(token_calls[0][3])
    assert token_request == {
        "repositories": ["blocks"],
        "permissions": {"actions": "write", "checks": "write", "pull_requests": "write"},
    }
    assert "metadata" not in token_request["permissions"]

    redirect_calls: list[tuple[str, dict[str, str]]] = []

    def redirect_request(
        url: str, headers: dict[str, str], _timeout: float
    ) -> tuple[int, dict[str, str], bytes]:
        redirect_calls.append((url, headers))
        if len(redirect_calls) == 1:
            return 302, {
                "Location": (
                    "https://productionresultssa12.blob.core.windows.net/"
                    "actions-results/fixture.zip?sig=fixture"
                )
            }, b""
        return 200, {}, b"bounded-zip-fixture"

    assert runtime.urllib_artifact_transport(
        "https://api.github.com/repos/dinkuskit/blocks/actions/artifacts/801/zip",
        {
            "Authorization": "Bearer fixture-installation-token",
            "Accept": "application/vnd.github+json",
            "User-Agent": "smoky-review-conductor/1",
        },
        15.0,
        request_once=redirect_request,
    ) == (200, b"bounded-zip-fixture")
    assert "Authorization" in redirect_calls[0][1]
    assert "Authorization" not in redirect_calls[1][1]
    assert redirect_calls[1][1] == {
        "Accept": "application/octet-stream",
        "User-Agent": "smoky-review-conductor/1",
    }

    rejected_targets = (
        "http://productionresultssa12.blob.core.windows.net/a?sig=x",
        "https://productionresultssa12.blob.core.windows.net.evil.example/a?sig=x",
        "https://blob.core.windows.net/a?sig=x",
        "https://user@productionresultssa12.blob.core.windows.net/a?sig=x",
        "https://productionresultssa12.blob.core.windows.net:444/a?sig=x",
        "https://productionresultssa12.blob.core.windows.net/a",
        "https://productionresultssa12.blob.core.windows.net/a?sig=x#fragment",
    )
    for rejected in rejected_targets:
        requests = 0

        def reject_request(
            _url: str, _headers: dict[str, str], _timeout: float
        ) -> tuple[int, dict[str, str], bytes]:
            nonlocal requests
            requests += 1
            return 302, {"Location": rejected}, b""

        try:
            runtime.urllib_artifact_transport(
                "https://api.github.com/repos/dinkuskit/blocks/actions/artifacts/801/zip",
                {"Authorization": "Bearer fixture-installation-token"},
                15.0,
                request_once=reject_request,
            )
        except runtime.GitHubApiError:
            pass
        else:
            raise AssertionError("unsafe artifact redirect must be rejected")
        assert requests == 1

    chained_calls = 0

    def chained_redirect(
        _url: str, _headers: dict[str, str], _timeout: float
    ) -> tuple[int, dict[str, str], bytes]:
        nonlocal chained_calls
        chained_calls += 1
        return 302, {
            "Location": (
                "https://productionresultssa12.blob.core.windows.net/"
                "actions-results/fixture.zip?sig=fixture"
            )
        }, b""

    try:
        runtime.urllib_artifact_transport(
            "https://api.github.com/repos/dinkuskit/blocks/actions/artifacts/801/zip",
            {"Authorization": "Bearer fixture-installation-token"},
            15.0,
            request_once=chained_redirect,
        )
    except runtime.GitHubApiError:
        pass
    else:
        raise AssertionError("artifact redirect chain must be rejected")
    assert chained_calls == 2

    for status, api_url in (
        (301, "https://api.github.com/repos/dinkuskit/blocks/actions/artifacts/801/zip"),
        (302, "https://api.github.com:444/repos/dinkuskit/blocks/actions/artifacts/801/zip"),
        (302, "https://api.github.com.evil.example/repos/dinkuskit/blocks/actions/artifacts/801/zip"),
    ):
        origin_calls = 0

        def rejected_origin(
            _url: str, _headers: dict[str, str], _timeout: float
        ) -> tuple[int, dict[str, str], bytes]:
            nonlocal origin_calls
            origin_calls += 1
            return status, {
                "Location": (
                    "https://productionresultssa12.blob.core.windows.net/"
                    "actions-results/fixture.zip?sig=fixture"
                )
            }, b""

        try:
            runtime.urllib_artifact_transport(
                api_url,
                {"Authorization": "Bearer fixture-installation-token"},
                15.0,
                request_once=rejected_origin,
            )
        except runtime.GitHubApiError:
            pass
        else:
            raise AssertionError("unexpected redirect status or API origin must be rejected")
        assert origin_calls == 1

    source = (ROOT / "tools/review_conductor_runtime.py").read_text(encoding="utf-8")
    assert "/pulls/{" not in source
    assert "def merge" not in source


def test_projection_create_claim_is_fail_closed(root: Path) -> None:
    _, config = fixture_config(root / "projection-claim")
    head = "c" * 40
    assert ingress(config, "pull_request", "projection-claim-pr", pr_payload(41, head))[0] == 202
    github = FailAfterCheckCreate()
    for _attempt in range(2):
        try:
            runtime.reconcile_projection(config, github)
        except (core.ContractError, RuntimeError):
            pass
        else:
            raise AssertionError("uncertain check creation must fail closed")
    assert len([call for call in github.calls if call[0] == "create_check"]) == 1
    connection = core.open_database(Path(config["paths"]["state_root"]))
    try:
        row = connection.execute(
            "SELECT openclaw_check_run_id, openclaw_check_create_state FROM projections"
        ).fetchone()
        assert row["openclaw_check_run_id"] is None
        assert row["openclaw_check_create_state"] == "creating"
    finally:
        connection.close()


def test_ready_label_reconciliation_converges_both_drift_directions(root: Path) -> None:
    _, config = fixture_config(root / "label-drift")
    pr = 42
    head = "e" * 40
    assert ingress(config, "pull_request", "label-drift-open", pr_payload(pr, head))[0] == 202
    github = FakeGitHub(label_present=True)
    runtime.reconcile_projection(config, github)
    assert github.label_present is False
    assert github.calls[-1][0] == "remove_label"

    connection = core.open_database(Path(config["paths"]["state_root"]))
    try:
        connection.execute(
            "UPDATE projections SET ready_label_applied = 1 WHERE pr_number = ?",
            (pr,),
        )
        connection.execute(
            "UPDATE heads SET state = 'ready_for_human_merge', rail = 'clawsweeper' WHERE pr_number = ? AND is_current = 1",
            (pr,),
        )
        connection.commit()
    finally:
        connection.close()
    github.label_present = False
    runtime.reconcile_projection(config, github)
    assert github.label_present is True
    assert github.calls[-1][0] == "add_label"
    connection = core.open_database(Path(config["paths"]["state_root"]))
    try:
        projection = connection.execute(
            """
            SELECT ready_label_applied, ready_label_reconcile_action,
                   ready_label_reconciled_at
            FROM projections WHERE pr_number = ?
            """,
            (pr,),
        ).fetchone()
        assert projection["ready_label_applied"] == 1
        assert projection["ready_label_reconcile_action"] == "ensure_present"
        assert projection["ready_label_reconciled_at"]
    finally:
        connection.close()


def test_ready_label_reconciliation_holds_during_clawsweeper_publication(root: Path) -> None:
    _, config = fixture_config(root / "label-publication-hold")
    pr = 43
    head = "f" * 40
    assert ingress(config, "pull_request", "label-hold-open", pr_payload(pr, head))[0] == 202
    github = FakeGitHub(label_present=True)
    runtime.reconcile_projection(config, github)
    assert github.label_present is False
    connection = core.open_database(Path(config["paths"]["state_root"]))
    try:
        connection.execute(
            "UPDATE heads SET state = 'clawsweeper_queued', rail = 'clawsweeper' WHERE pr_number = ? AND is_current = 1",
            (pr,),
        )
        connection.commit()
    finally:
        connection.close()
    github.calls.clear()
    queued = runtime.reconcile_projection(config, github)
    assert queued["projected"][0]["ready_label_action"] == "ensure_absent_before_clawsweeper_publication"
    assert github.calls[-1][0] == "remove_label"
    github.label_present = True
    connection = core.open_database(Path(config["paths"]["state_root"]))
    try:
        connection.execute(
            "UPDATE heads SET state = 'clawsweeper_running' WHERE pr_number = ? AND is_current = 1",
            (pr,),
        )
        connection.commit()
    finally:
        connection.close()
    github.calls.clear()
    running = runtime.reconcile_projection(config, github)
    assert running["projected"][0]["ready_label_action"] == "hold_for_clawsweeper_publication"
    assert github.label_present is True
    assert not any(call[0] in {"add_label", "remove_label"} for call in github.calls)
    connection = core.open_database(Path(config["paths"]["state_root"]))
    try:
        projection = connection.execute(
            "SELECT ready_label_applied, ready_label_reconcile_action FROM projections WHERE pr_number = ?",
            (pr,),
        ).fetchone()
        assert projection["ready_label_applied"] == 0
        assert projection["ready_label_reconcile_action"] == "hold_for_clawsweeper_publication"
        connection.execute(
            "UPDATE heads SET state = 'clawsweeper_failed' WHERE pr_number = ? AND is_current = 1",
            (pr,),
        )
        connection.commit()
    finally:
        connection.close()
    github.calls.clear()
    failed = runtime.reconcile_projection(config, github)
    assert failed["projected"][0]["ready_label_action"] == "ensure_absent"
    assert github.label_present is False
    assert github.calls[-1][0] == "remove_label"


def test_projection_terminalizes_superseded_heads_and_epochs(root: Path) -> None:
    _, config = fixture_config(root / "projection-superseded")
    pr = 43
    head_a = "a" * 40
    head_b = "b" * 40
    github = FakeGitHub()

    assert ingress(config, "pull_request", "projection-a-open", pr_payload(pr, head_a))[0] == 202
    runtime.reconcile_projection(config, github)
    epoch_zero_ids = {
        call[1]: call[0]
        for call in (
            (501, "OpenClaw Review Rail"),
            (502, "ClawSweeper Review Rail"),
        )
    }

    assert ingress(
        config,
        "pull_request",
        "projection-b-sync",
        pr_payload(
            pr,
            head_b,
            action="synchronize",
            updated_at="2026-08-28T03:20:00Z",
        ),
    )[0] == 202
    runtime.reconcile_projection(config, github)
    a_updates = [
        call for call in github.calls
        if call[0] == "update_check" and call[2] in epoch_zero_ids and call[3] == head_a
    ]
    assert len(a_updates) == 2
    assert {call[1] for call in a_updates} == set(epoch_zero_ids.values())
    assert {call[-1] for call in a_updates} == {"skipped"}

    assert ingress(
        config,
        "pull_request",
        "projection-a-return",
        pr_payload(
            pr,
            head_a,
            action="synchronize",
            updated_at="2026-08-28T03:30:00Z",
        ),
    )[0] == 202
    runtime.reconcile_projection(config, github)
    returned = current(config, pr)
    assert returned["head_sha"] == head_a
    assert returned["review_epoch"] == 1
    assert returned["state"] == "ci_running"

    connection = core.open_database(Path(config["paths"]["state_root"]))
    try:
        rows = connection.execute(
            """
            SELECT head_sha, review_epoch, last_projected_state
            FROM projections WHERE pr_number = ?
            ORDER BY head_sha, review_epoch
            """,
            (pr,),
        ).fetchall()
        projection_states = {
            (row["head_sha"], row["review_epoch"]): row["last_projected_state"]
            for row in rows
        }
        assert projection_states[(head_a, 0)] == "superseded"
        assert projection_states[(head_b, 0)] == "superseded"
        assert projection_states[(head_a, 1)] == "ci_running"
    finally:
        connection.close()


def test_closed_projection_filter_removes_only_target_ready_label(root: Path) -> None:
    _, config = fixture_config(root / "closed-filter")
    github = FakeGitHub(label_present=True)
    for pr, head in ((61, "6" * 40), (62, "7" * 40)):
        assert ingress(config, "pull_request", f"open-{pr}", pr_payload(pr, head))[0] == 202
        runtime.reconcile_projection(config, github, pr_number=pr)
        connection = core.open_database(Path(config["paths"]["state_root"]))
        try:
            connection.execute(
                "UPDATE projections SET ready_label_applied=1 WHERE pr_number=?", (pr,)
            )
            connection.execute(
                "UPDATE heads SET state='closed' WHERE pr_number=? AND is_current=1", (pr,)
            )
            connection.commit()
        finally:
            connection.close()
    github.calls.clear()
    runtime.reconcile_projection(config, github, pr_number=61)
    removals = [call for call in github.calls if call[0] == "remove_label"]
    assert removals == [("remove_label", 61, runtime.READY_LABEL)]
    connection = core.open_database(Path(config["paths"]["state_root"]))
    try:
        rows = connection.execute(
            "SELECT pr_number, ready_label_applied FROM projections WHERE pr_number IN (61,62) ORDER BY pr_number"
        ).fetchall()
        assert [(row["pr_number"], row["ready_label_applied"]) for row in rows] == [(61, 0), (62, 1)]
    finally:
        connection.close()


def test_closed_head_without_projection_still_removes_ready_label(root: Path) -> None:
    _, config = fixture_config(root / "closed-without-projection")
    pr = 63
    head = "8" * 40
    assert ingress(config, "pull_request", "open-no-projection", pr_payload(pr, head))[0] == 202
    connection = core.open_database(Path(config["paths"]["state_root"]))
    try:
        connection.execute(
            "UPDATE heads SET state='closed' WHERE pr_number=? AND is_current=1", (pr,)
        )
        connection.commit()
        assert connection.execute(
            "SELECT 1 FROM projections WHERE pr_number=?", (pr,)
        ).fetchone() is None
    finally:
        connection.close()
    github = FakeGitHub(label_present=True)
    result = runtime.reconcile_projection(config, github, pr_number=pr)
    assert [(call[0], call[1]) for call in github.calls if call[0] == "remove_label"] == [("remove_label", pr)]
    assert result["projected"][0]["visible_state"] == "closed"
    github.calls.clear()
    assert runtime.reconcile_projection(config, github, pr_number=pr)["projected"] == []
    assert github.calls == []


def test_v1_config_upgrade_and_failed_projection_compatibility(root: Path) -> None:
    legacy = json.loads(CORE_CONFIG.read_text(encoding="utf-8"))
    legacy["openclaw"].pop("transport")
    legacy["clawsweeper"].pop("workflow_name")
    legacy["clawsweeper"].pop("workflow_path")
    legacy_path = write_json(root / "compatibility" / "legacy-v1.json", legacy)
    loaded = core.load_config(legacy_path)
    assert loaded["schema"] == "smoky.review-conductor.config.v1"
    assert loaded["openclaw"]["transport"] == "origin"
    assert loaded["clawsweeper"]["workflow_name"] is None
    assert loaded["clawsweeper"]["workflow_path"] is None

    projection = core.state_projection(
        {"state": "clawsweeper_failed", "rail": "clawsweeper"}
    )
    assert projection["checks"] == {
        "OpenClaw Review Rail": "success",
        "ClawSweeper Review Rail": "failure",
    }
    assert projection["ready_for_human_label"] is False

    expected = {
        "ci_failed": ("skipped", "skipped"),
        "closed": ("cancelled", "cancelled"),
        "closed_merged": ("cancelled", "cancelled"),
        "openclaw_failed": ("failure", "skipped"),
        "clawsweeper_failed": ("success", "failure"),
        "ready_for_human_merge": ("success", "success"),
    }
    for state, pair in expected.items():
        mapped = core.state_projection({"state": state, "rail": "clawsweeper"})
        assert tuple(mapped["checks"].values()) == pair
        for check_state in pair:
            payload = runtime.check_payload(
                "OpenClaw Review Rail", "d" * 40, "review-conductor:test", check_state
            )
            assert payload["status"] == "completed"
            assert payload["conclusion"] == check_state
    stale = core.state_projection(
        {"state": "ci_running", "rail": None, "is_current": 0}
    )
    assert set(stale["checks"].values()) == {"skipped"}


def fake_smoky(path: Path, head: str) -> None:
    path.write_text(
        f"#!{sys.executable}\n"
        "import json, sys\n"
        f"head = {head!r}\n"
        "result = 'completed' if 'spark-openclaw-materialize-worktree' in sys.argv else 'queued'\n"
        "print(json.dumps({'result': result, 'commit_sha': head, 'proof_path': '/external/PROOF.md'}))\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def test_full_event_driven_replay_projection(root: Path) -> None:
    config_path, config = fixture_config(root / "e2e")
    pr = 50
    old_head = "6" * 40
    head = "7" * 40
    next_head = "8" * 40
    assert ingress(config, "pull_request", "e2e-open", pr_payload(pr, old_head))[1]["result"] == "accepted"
    failed = ingress(config, "workflow_run", "e2e-ci-fail", ci_payload(pr, old_head, "failure", 5001))[1]
    assert failed["result"] == "accepted"
    assert actions(config, pr, "openclaw.enqueue") == []
    assert current(config, pr)["state"] == "ci_failed"

    synchronized = pr_payload(pr, head, action="synchronize", updated_at="2026-08-28T03:20:00Z")
    assert ingress(config, "pull_request", "e2e-sync", synchronized)[1]["result"] == "accepted"
    success_payload = ci_payload(pr, head, "success", 5002, created_at="2026-08-28T03:21:00Z")
    first = ingress(config, "workflow_run", "e2e-ci-success", success_payload)[1]
    duplicate = ingress(config, "workflow_run", "e2e-ci-success", success_payload)[1]
    second_delivery = ingress(config, "workflow_run", "e2e-ci-success-2", success_payload)[1]
    assert first["result"] == "accepted" and duplicate["result"] == "duplicate_delivery"
    assert second_delivery["result"] == "accepted"
    open_actions = actions(config, pr, "openclaw.enqueue")
    assert len(open_actions) == 1

    github = FakeGitHub()
    first_projection = runtime.reconcile_projection(config, github)
    assert first_projection["projected"][0]["head_sha"] == head
    assert len([call for call in github.calls if call[0] == "create_check"]) == 2
    runtime.reconcile_projection(config, github)
    assert len([call for call in github.calls if call[0] == "create_check"]) == 2

    fake = root / "e2e" / "fake-smoky"
    fake_smoky(fake, head)
    config["spark"]["smoky_path"] = str(fake)
    write_json(config_path, config)
    worker = runtime.drain_actions(config, github)
    assert worker["github_ci_polled"] is False
    assert worker["actions"][0]["result"] == "dispatched"
    open_action = actions(config, pr, "openclaw.enqueue")[0]
    assert open_action["status"] == "dispatched"

    queued_artifact = openclaw_artifact(root / "e2e", config, open_action, status="queued")
    try:
        runtime.bridge_openclaw(config, queued_artifact)
    except core.ContractError:
        pass
    else:
        raise AssertionError("queue submission must not be treated as terminal OpenClaw proof")
    wrong_head_artifact = openclaw_artifact(root / "e2e", config, open_action, head_override=old_head)
    try:
        runtime.bridge_openclaw(config, wrong_head_artifact)
    except core.ContractError:
        pass
    else:
        raise AssertionError("wrong-head OpenClaw terminal result must be stale")

    terminal = openclaw_artifact(root / "e2e", config, open_action)
    openclaw_inbox_artifact = (
        Path(config["spark"]["terminal_inbox"]) / "openclaw-current.terminal.json"
    )
    openclaw_inbox_artifact.write_bytes(terminal.read_bytes())
    github = CompetingHandoffGitHub(config, label_present=True)
    bridge_cycle = {"bridge_drain": runtime.drain_bridge_inboxes(config)}
    bridge_cycle["second_action_drain"] = runtime.drain_actions(config, github)
    bridge_rows = bridge_cycle["bridge_drain"]["artifacts"]
    assert bridge_rows[0]["rail"] == "openclaw"
    assert runtime.bridge_receipt_path(openclaw_inbox_artifact).is_file()
    claw_actions = actions(config, pr, "clawsweeper.dispatch")
    assert len(claw_actions) == 1
    assert bridge_cycle["second_action_drain"]["actions"][0]["kind"] == "clawsweeper.dispatch"
    duplicate_bridge = runtime.drain_bridge_inboxes(config)
    assert duplicate_bridge["artifacts"][0]["result"] == "already_processed"
    claw_action = actions(config, pr, "clawsweeper.dispatch")[0]
    assert claw_action["status"] == "dispatched"
    assert github.cleanup_action_statuses == ["preparing"]
    assert github.competing_dispatch_errors == ["ClawSweeper action is not safely claimable"]
    label_cleanup_index = next(
        index for index, call in enumerate(github.calls) if call[0] == "remove_label"
    )
    dispatch_index = next(
        index for index, call in enumerate(github.calls) if call[0] == "dispatch_clawsweeper"
    )
    assert label_cleanup_index < dispatch_index
    assert github.label_present is False
    dispatches = [call for call in github.calls if call[0] == "dispatch_clawsweeper"]
    assert len(dispatches) == 1
    exact = dispatches[0][1]
    assert exact == {"pr_number": pr, "base_sha": BASE, "head_sha": head, "publish": True}
    github.label_present = True
    github.calls.clear()
    queued_projection = runtime.reconcile_projection(config, github)
    assert queued_projection["projected"][0]["ready_label_action"] == "hold_for_clawsweeper_publication"
    assert github.label_present is True
    assert not any(call[0] in {"add_label", "remove_label"} for call in github.calls)

    run_id = 5050
    claw_webhook = ingress(
        config, "workflow_run", "e2e-claw-terminal", clawsweeper_workflow_payload(run_id)
    )[1]
    assert claw_webhook["result"] == "accepted"
    same_run = ingress(
        config, "workflow_run", "e2e-claw-terminal-repeat", clawsweeper_workflow_payload(run_id)
    )[1]
    assert same_run["result"] == "duplicate"
    conflicting_run = clawsweeper_workflow_payload(run_id)
    conflicting_run["workflow_run"]["conclusion"] = "failure"
    assert ingress(
        config, "workflow_run", "e2e-claw-terminal-conflict", conflicting_run
    )[0] == 400
    connection = core.open_database(Path(config["paths"]["state_root"]))
    try:
        rail_row = connection.execute(
            "SELECT status, verdict FROM rail_workflow_runs WHERE workflow_run_id = ?",
            (str(run_id),),
        ).fetchone()
        assert rail_row is not None
        assert rail_row["status"] == "terminal_pending_verdict_bridge"
        assert rail_row["verdict"] is None
    finally:
        connection.close()
    wrong_claw = clawsweeper_artifact(
        root / "e2e", config, claw_action, run_id, head_override=old_head
    )
    try:
        runtime.bridge_clawsweeper(config, wrong_claw)
    except core.ContractError:
        pass
    else:
        raise AssertionError("wrong-head ClawSweeper result must not advance state")
    claw_terminal = clawsweeper_artifact(root / "e2e", config, claw_action, run_id)
    claw_inbox_artifact = (
        Path(config["clawsweeper_bridge"]["terminal_inbox"])
        / "clawsweeper-current.terminal.json"
    )
    claw_inbox_artifact.write_bytes(claw_terminal.read_bytes())
    claw_cycle = {"bridge_drain": runtime.drain_bridge_inboxes(config)}
    claw_receipts = [
        item for item in claw_cycle["bridge_drain"]["artifacts"]
        if item["rail"] == "clawsweeper"
    ]
    assert claw_receipts[0]["result"] == "accepted"
    assert runtime.bridge_receipt_path(claw_inbox_artifact).is_file()
    assert current(config, pr)["state"] == "ready_for_human_merge"

    before_labels = len([call for call in github.calls if call[0] == "add_label"])
    runtime.reconcile_projection(config, github)
    runtime.reconcile_projection(config, github)
    after_labels = len([call for call in github.calls if call[0] == "add_label"])
    assert after_labels == before_labels + 2
    assert github.label_present is True
    assert current(config, pr)["state"] == "ready_for_human_merge"

    assert ingress(
        config,
        "pull_request",
        "e2e-next-head",
        pr_payload(pr, next_head, action="synchronize", updated_at="2026-08-28T03:40:00Z"),
    )[1]["result"] == "accepted"
    runtime.reconcile_projection(config, github)
    assert github.calls[-1][0] == "remove_label"
    assert github.label_present is False
    stale_terminal = openclaw_artifact(root / "e2e", config, open_action)
    try:
        runtime.bridge_openclaw(config, stale_terminal)
    except core.ContractError:
        pass
    else:
        raise AssertionError("old-head terminal proof must stay historical")

    closed = ingress(
        config,
        "pull_request",
        "e2e-close",
        pr_payload(pr, next_head, action="closed", updated_at="2026-08-28T03:41:00Z"),
    )[1]
    assert closed["result"] == "accepted"
    assert current(config, pr)["state"] == "closed"
    runtime.reconcile_projection(config, github)
    terminal_check_updates = [
        call for call in github.calls if call[0] == "update_check"
    ][-2:]
    assert [call[-1] for call in terminal_check_updates] == [
        "cancelled",
        "cancelled",
    ]
    reopened = ingress(
        config,
        "pull_request",
        "e2e-reopen",
        pr_payload(pr, next_head, action="reopened", updated_at="2026-08-28T03:42:00Z"),
    )[1]
    assert reopened["result"] == "accepted"
    assert current(config, pr)["state"] == "ci_running"
    assert current(config, pr)["review_epoch"] == 1



def test_repair_owner_handoff_and_two_cycle_cap(root: Path) -> None:
    _, config = fixture_config(root / "repairs")
    pr = 60
    head_digits = ("9", "a", "b")
    first_head = head_digits[0] * 40
    assert ingress(config, "pull_request", "repair-open", pr_payload(pr, first_head))[0] == 202
    for cycle, digit in enumerate(head_digits):
        head = digit * 40
        if cycle:
            assert ingress(
                config,
                "pull_request",
                f"repair-sync-{cycle}",
                pr_payload(pr, head, action="synchronize", updated_at=f"2026-08-28T04:0{cycle}:00Z"),
            )[0] == 202
        assert ingress(
            config,
            "workflow_run",
            f"repair-ci-{cycle}",
            ci_payload(pr, head, "success", 6000 + cycle, created_at=f"2026-08-28T04:1{cycle}:00Z"),
        )[0] == 202
        action = next(
            item for item in actions(config, pr, "openclaw.enqueue")
            if item["head_sha"] == head
        )
        connection = core.open_database(Path(config["paths"]["state_root"]))
        try:
            connection.execute(
                "UPDATE actions SET status = 'dispatched' WHERE action_id = ?", (action["action_id"],)
            )
            connection.commit()
        finally:
            connection.close()
        finding_artifact = openclaw_artifact(
            root / "repairs", config, action, status="needs-human", clean=False, findings=1
        )
        finding_result = runtime.bridge_openclaw(config, finding_artifact)
        assert finding_result["state"] == "awaiting_adjudication"
        request_id = json.loads(action["payload_json"])["queue_request_id"]
        row = current(config, pr)
        adjudication = {
            "schema": core.INTERNAL_EVENT_SCHEMA,
            "event_id": f"repair-adjudication-{cycle}",
            "type": "adjudication.completed",
            "repository": "dinkuskit/blocks",
            "pr_number": pr,
            "base_sha": BASE,
            "head_sha": head,
            "request_id": request_id,
            "rail": "openclaw",
            "classifications": ["required_fix"],
            "reviewer_actor": "independent-reviewer",
            "repair_owner": "alice",
            "proof_ref": f"proof/adjudication/{cycle}/ADJUDICATION.md",
        }
        result = core.ingest_internal_event(
            config_path=CORE_CONFIG,
            state_root=Path(config["paths"]["state_root"]),
            event_payload=adjudication,
        )
        assert result["state"] == "repair_required"
        repair = next(
            item for item in actions(config, pr, "repair.route")
            if item["head_sha"] == head
        )
        repair_payload = json.loads(repair["payload_json"])
        assert repair_payload["repair_owner"] == "alice"
        assert repair_payload["mutation_owner_count"] == 1
        if cycle >= 2:
            assert row["repair_cycle"] == 2
            assert repair_payload["repair_cycle"] == 2
    assert current(config, pr)["state"] == "repair_required"
    assert current(config, pr)["repair_cycle"] == 2


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="review-conductor-activation-test-") as temp_name:
        root = Path(temp_name)
        test_http_ingress_limits_signature_dedupe_and_allowlists(root)
        test_authentication_precedes_parser_state_and_header_deadline(root)
        test_abandoned_claim_recovery_is_kind_safe(root)
        test_abandoned_clawsweeper_preparation_is_safely_retryable(root)
        test_epoch_reuse_cannot_revive_superseded_action(root)
        test_github_client_endpoint_and_credential_boundaries(root)
        test_projection_create_claim_is_fail_closed(root)
        test_ready_label_reconciliation_converges_both_drift_directions(root)
        test_ready_label_reconciliation_holds_during_clawsweeper_publication(root)
        test_projection_terminalizes_superseded_heads_and_epochs(root)
        test_v1_config_upgrade_and_failed_projection_compatibility(root)
        test_full_event_driven_replay_projection(root)
        test_repair_owner_handoff_and_two_cycle_cap(root)
    print("review conductor shared adapter tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
