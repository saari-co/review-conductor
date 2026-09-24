#!/usr/bin/env python3
"""Contract and replay proof for the current-user Review Conductor."""

from __future__ import annotations

import hashlib
import hmac
import io
import json
import os
import pwd
import sqlite3
import shutil
import subprocess
import sys
import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
import review_conductor as core  # noqa: E402
import review_conductor_runtime as runtime  # noqa: E402
import review_conductor_userland as userland  # noqa: E402
import review_conductor_userland_launcher as launcher  # noqa: E402


SECRET = "fixture-userland-webhook-secret"
BASE = "1" * 40
HEAD = "2" * 40


def config_fixture(root: Path) -> dict[str, Any]:
    home = root / "home"
    home.mkdir()
    config = userland.load_config(
        userland.DEFAULT_CONFIG,
        home=home,
        source_root=ROOT,
    )
    for path in (
        Path(config["paths"]["state_root"]),
        Path(config["paths"]["proof_root"]),
        Path(config["paths"]["blocks_checkout"]),
        Path(config["spark"]["terminal_inbox"]),
        Path(config["clawsweeper_bridge"]["terminal_inbox"]),
    ):
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    config["source_root"] = str((root / "source").resolve())
    Path(config["source_root"]).mkdir(exist_ok=True)
    return config


def signed_headers(event_type: str, delivery: str, body: bytes) -> dict[str, str]:
    signature = "sha256=" + hmac.new(
        SECRET.encode(), body, hashlib.sha256
    ).hexdigest()
    return {
        "content-type": "application/json",
        "x-github-event": event_type,
        "x-github-delivery": delivery,
        "x-hub-signature-256": signature,
    }


def ingress(
    config: dict[str, Any],
    event_type: str,
    delivery: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    body = core.canonical_json(payload).encode()
    status, receipt = runtime.handle_webhook_request(
        config,
        method="POST",
        path="/github/webhook",
        headers=signed_headers(event_type, delivery, body),
        body=body,
        secret=SECRET,
    )
    assert status == 202, receipt
    return receipt


def pr_payload(pr: int, head: str = HEAD) -> dict[str, Any]:
    return {
        "action": "opened",
        "repository": {"id": 1306882611, "full_name": "dinkuskit/blocks"},
        "pull_request": {
            "number": pr,
            "base": {"ref": "main", "sha": BASE},
            "head": {"sha": head},
            "user": {"login": "implementer"},
            "updated_at": "2026-08-29T20:00:00Z",
            "merged": False,
        },
    }


def ci_payload(
    pr: int, run_id: int, head: str = HEAD, *, conclusion: str = "success"
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
            "created_at": "2026-08-29T20:01:00Z",
            "pull_requests": [
                {
                    "number": pr,
                    "base": {"ref": "main", "sha": BASE},
                    "head": {"sha": head},
                }
            ],
        },
    }


def claw_workflow_payload(run_id: int, *, conclusion: str = "success") -> dict[str, Any]:
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
            "conclusion": conclusion,
            "head_sha": BASE,
            "created_at": "2026-08-29T20:03:00Z",
            "pull_requests": [],
        },
    }


def set_trusted_enrollment(
    config: dict[str, Any], review_conductor: str, legacy_xapi: str
) -> None:
    enrollment = dict(config.get("enrollment") or {})
    enrollment["review_conductor"] = review_conductor
    enrollment["legacy_xapi"] = legacy_xapi
    config["enrollment"] = enrollment


def set_head(
    config: dict[str, Any],
    pr: int,
    *,
    state: str,
    rail: str | None = None,
    blocker: str | None = None,
) -> sqlite3.Row:
    connection = core.open_database(Path(config["paths"]["state_root"]))
    try:
        connection.execute(
            """
            UPDATE heads
            SET state = ?, rail = ?, blocker = ?
            WHERE pr_number = ? AND is_current = 1
            """,
            (state, rail, blocker, pr),
        )
        connection.commit()
        row = connection.execute(
            "SELECT * FROM heads WHERE pr_number = ? AND is_current = 1",
            (pr,),
        ).fetchone()
        assert row is not None
        return row
    finally:
        connection.close()


def insert_quality(config: dict[str, Any], pr: int, ready_qualified: Any) -> None:
    row = current(config, pr)
    connection = core.open_database(Path(config["paths"]["state_root"]))
    try:
        userland.ensure_userland_tables(connection)
        connection.execute(
            """
            INSERT INTO clawsweeper_quality(
              repository, pr_number, base_sha, head_sha, review_epoch,
              workflow_run_id, overall_tier, proof_tier, proof_status,
              ready_qualified, report_sha256, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["repository"],
                pr,
                row["base_sha"],
                row["head_sha"],
                row["review_epoch"],
                "1",
                "S",
                "S",
                "sufficient",
                ready_qualified,
                "a" * 64,
                core.utc_now(),
            ),
        )
        connection.commit()
    finally:
        connection.close()


def queued_notification_outcomes(config: dict[str, Any]) -> list[dict[str, Any]]:
    connection = core.open_database(Path(config["paths"]["state_root"]))
    try:
        return [
            json.loads(row["payload_json"])["orchestration_outcome"]
            for row in connection.execute(
                "SELECT payload_json FROM notification_deliveries ORDER BY channel"
            )
        ]
    finally:
        connection.close()


def notification_statuses(config: dict[str, Any]) -> list[tuple[str, str]]:
    connection = core.open_database(Path(config["paths"]["state_root"]))
    try:
        return [
            (row["channel"], row["status"])
            for row in connection.execute(
                "SELECT channel, status FROM notification_deliveries ORDER BY channel, event_key"
            )
        ]
    finally:
        connection.close()


class UnavailableNotifier:
    def send(self, channel: str, _message: str) -> None:
        raise userland.NotificationUnavailable(f"{channel} fixture unavailable")


def current(config: dict[str, Any], pr: int) -> sqlite3.Row:
    connection = core.open_database(Path(config["paths"]["state_root"]))
    try:
        row = core.current_head(connection, "dinkuskit/blocks", pr)
        assert row is not None
        return row
    finally:
        connection.close()


def action(config: dict[str, Any], pr: int, kind: str) -> sqlite3.Row:
    connection = core.open_database(Path(config["paths"]["state_root"]))
    try:
        rows = connection.execute(
            "SELECT * FROM actions WHERE pr_number = ? AND kind = ? ORDER BY created_at",
            (pr, kind),
        ).fetchall()
        assert len(rows) == 1
        return rows[0]
    finally:
        connection.close()


def mark_dispatched(config: dict[str, Any], action_id: str) -> None:
    connection = core.open_database(Path(config["paths"]["state_root"]))
    try:
        connection.execute(
            "UPDATE actions SET status = 'dispatched', receipt_json = ? WHERE action_id = ?",
            (core.canonical_json({"result": "dispatched"}), action_id),
        )
        connection.commit()
    finally:
        connection.close()


def openclaw_run_fixture(root: Path, request_id: str, head: str = HEAD) -> Path:
    run = root / "source/runs/spark-openclaw-autoreview-runs" / ("spark-openclaw-autoreview-20260915T133456Z-" + str(int(hashlib.sha256(request_id.encode()).hexdigest()[:8], 16)))
    run.mkdir(parents=True)
    (run / "PROOF.md").write_text(
        "# OpenClaw proof\n\nExact-head review clean with zero findings.\n",
        encoding="utf-8",
    )
    (run / "REQUEST_STATUS.json").write_text(
        json.dumps(
            {
                "id": request_id,
                "operator_id": "review-conductor",
                "commit_sha": head,
                "submitted_head": head,
                "status": "completed",
                "review_clean": True,
                "review_finding_count": 0,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return run.resolve()


class SparkStatusRunner:
    def __init__(self, run: Path, *, result: str = "completed") -> None:
        self.run = run
        self.result = result
        self.calls: list[list[str]] = []

    def __call__(self, command: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append(command)
        return subprocess.CompletedProcess(
            command,
            0 if self.result == "completed" else 3,
            stdout=json.dumps(
                {
                    "result": self.result,
                    "proof_path": str(self.run / "PROOF.md"),
                    "commit_sha": HEAD,
                }
            )
            + "\n",
            stderr="",
        )


def claw_bundle(
    *,
    run_id: int,
    pr: int,
    overall: str = "B",
    proof: str = "B",
    proof_status: str = "sufficient",
    maintainer_required: bool = False,
    finding: bool = False,
    decision: str | None = None,
    process_gates: list[str] | None = None,
    needs_contributor_action: bool = False,
    base_sha: str = BASE,
    head_sha: str = HEAD,
    review_epoch: int | None = None,
) -> bytes:
    finding_text = (
        "\n## Review Findings\n\n- **[P1] Fix the exact regression:** `src/example.ts:1`\n  - body: bounded fixture finding\n"
        if finding
        else "\n## Review Findings\n\nNo contributor-facing findings.\n"
    )
    report = "\n".join(
        [
            "---",
            f"number: {pr}",
            "repository: dinkuskit/blocks",
            "review_status: complete",
            "review_terminal_failure: false",
            f"main_sha: {base_sha}",
            f"pull_head_sha: {head_sha}",
            *(
                [f"review_epoch: {review_epoch}"]
                if review_epoch is not None
                else []
            ),
            f"pr_rating_overall: {overall}",
            f"pr_rating_proof: {proof}",
            f"real_behavior_proof_status: {proof_status}",
            f"real_behavior_proof_needs_contributor_action: {str(needs_contributor_action).lower()}",
            "maintainer_decision: "
            + json.dumps(
                {
                    "required": maintainer_required,
                    "kind": "manual_review" if maintainer_required else "none",
                },
                separators=(",", ":"),
            ),
            *(
                [f"decision: {decision}"]
                if decision
                else []
            ),
            *(
                [
                    "process_gates: "
                    + json.dumps(process_gates, separators=(",", ":"))
                ]
                if process_gates is not None
                else []
            ),
            "---",
            "",
            f"# DinkusKit Blocks PR #{pr}",
            finding_text,
        ]
    ).encode()
    report_path = f"review/{pr}.md"
    manifest = {
        "schema_version": 1,
        "workflow": {
            "repository": "dinkuskit/blocks",
            "source_sha": "3" * 40,
            "run_id": str(run_id),
            "run_attempt": 1,
            "producer_job": "review",
        },
        "target": {
            "repo": "dinkuskit/blocks",
            "branch": "main",
            "item_number": pr,
            "item_kind": "pull_request",
        },
        "review": {
            "artifact_present": True,
            "live_proceeded": True,
            "live_terminal_missing": False,
        },
        "files": [
            {
                "path": report_path,
                "bytes": len(report),
                "sha256": hashlib.sha256(report).hexdigest(),
            }
        ],
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr(report_path, report)
    return output.getvalue()


class FakeGitHub:
    def __init__(self, run_id: int, bundle: bytes) -> None:
        self.run_id = run_id
        self.bundle = bundle

    def list_run_artifacts(self, run_id: int) -> list[dict[str, Any]]:
        assert run_id == self.run_id
        return [
            {
                "id": 9001,
                "name": f"dinkuskit-native-review-{run_id}-1",
                "expired": False,
            }
        ]

    def download_artifact(self, artifact_id: int) -> bytes:
        assert artifact_id == 9001
        return self.bundle


class EmptyGitHub:
    def list_run_artifacts(self, _run_id: int) -> list[dict[str, Any]]:
        return []


VERBOSE_TERMINAL_FRAGMENTS = (
    "Review Conductor",
    "Bobby",
    "Repair cycle",
    "overall tier",
    "proof sufficient",
    "No merge was attempted",
    "exact-head",
    "Spark-2",
    "transcript",
    "progress",
)


def concise_ready(pr: int) -> str:
    return f"dinkuskit/blocks#{pr} ready to merge https://github.com/dinkuskit/blocks/pull/{pr}"


def concise_blocked(pr: int, reason: str) -> str:
    return f"dinkuskit/blocks#{pr} blocked — {reason} https://github.com/dinkuskit/blocks/pull/{pr}"


def assert_exact_terminal_messages(notifier: FakeNotifier, expected: str) -> None:
    assert [channel for channel, _message in notifier.sent] == [
        "openclaw_context",
        "discord",
        "signal",
    ]
    for _channel, message in notifier.sent:
        assert message == expected
        for fragment in VERBOSE_TERMINAL_FRAGMENTS:
            assert fragment not in message


class FakeNotifier:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send(self, channel: str, message: str) -> None:
        self.sent.append((channel, message))


class UncertainNotifier:
    def send(self, _channel: str, _message: str) -> None:
        raise userland.UserlandError("fixture delivery outcome is uncertain")


class FakeServiceAccounts:
    def __init__(self, config: dict[str, Any], *, wrong_scope: bool = False) -> None:
        self.config = config
        self.wrong_scope = wrong_scope
        self.calls: list[list[str]] = []
        self.tokens = {
            capability: f"ops_fixture_{index}_{'a' * 32}"
            for index, capability in enumerate(launcher.CAPABILITIES, start=1)
        }

    def __call__(self, command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        self.calls.append(command)
        base_environment = {
            "HOME": self.config["home"],
            "USER": pwd.getpwuid(os.getuid()).pw_name,
            "LOGNAME": pwd.getpwuid(os.getuid()).pw_name,
            "PATH": "/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin",
            "TMPDIR": "/tmp",
            "LC_ALL": "C",
        }
        assert command[1:] == ["vault", "list", "--format", "json"]
        token = kwargs["env"]["OP_SERVICE_ACCOUNT_TOKEN"]
        assert kwargs["env"] == {
            **base_environment,
            "OP_BIOMETRIC_UNLOCK_ENABLED": "false",
            "OP_LOAD_DESKTOP_APP_SETTINGS": "false",
            "OP_SERVICE_ACCOUNT_TOKEN": token,
        }
        capability = next(key for key, value in self.tokens.items() if value == token)
        vault = self.config["onepassword"]["domains"][capability]["runtime_vault"]
        if self.wrong_scope and capability == launcher.CAPABILITIES[1]:
            vault = "Wrong Runtime Vault"
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps([{"id": f"vault-{capability}", "name": vault}]).encode("utf-8"),
            stderr=b"",
        )


class FakeProcess:
    def __init__(self, command: list[str], kwargs: dict[str, Any]) -> None:
        self.command = command
        self.kwargs = kwargs
        self.returncode = 0
        self.terminated = False

    def poll(self) -> int:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout: int) -> int:
        return self.returncode

    def kill(self) -> None:
        self.returncode = -9


def prepare_openclaw(config: dict[str, Any], root: Path, pr: int) -> None:
    ingress(config, "pull_request", f"pr-{pr}", pr_payload(pr))
    ingress(config, "workflow_run", f"ci-{pr}", ci_payload(pr, 1000 + pr))
    queued = action(config, pr, "openclaw.enqueue")
    mark_dispatched(config, queued["action_id"])
    request_id = json.loads(queued["payload_json"])["queue_request_id"]
    run = openclaw_run_fixture(root, request_id)
    result = userland.collect_openclaw_terminals(
        config,
        dry_run=False,
        runner=SparkStatusRunner(run),
    )
    assert result == [{"request_id": request_id, "result": "terminal_materialized"}]
    bridge = runtime.drain_bridge_inboxes(config)
    assert any(item.get("rail") == "openclaw" for item in bridge["artifacts"])
    assert current(config, pr)["state"] == "clawsweeper_queued"


def prepare_clawsweeper_run(config: dict[str, Any], pr: int, run_id: int) -> sqlite3.Row:
    claw_action = action(config, pr, "clawsweeper.dispatch")
    mark_dispatched(config, claw_action["action_id"])
    ingress(config, "workflow_run", f"claw-{run_id}", claw_workflow_payload(run_id))
    return claw_action


def test_userland_config_has_no_root_or_custom_identity_route() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        config = config_fixture(root)
        serialized = json.dumps(config, sort_keys=True)
        assert config["mode"] == "cp1-current-user"
        assert config["legacy_cp1_route"] == "disabled"
        assert "/var/empty" not in serialized
        assert "_smokyreview" not in serialized
        assert "_smokytunnel" not in serialized
        assert "LaunchDaemon" not in serialized
        assert config["github_app"]["app_id"] == 4751895
        assert config["github_app"]["installation_id"] == 157287342
        assert config["notifications"]["agent_id"] == "main"
        assert set(config["onepassword"]["domains"]) == set(launcher.CAPABILITIES)


def test_one_attended_three_paste_bootstrap_writes_exact_vault_domains() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        config = config_fixture(root)
        fake = FakeServiceAccounts(config)
        prompts: list[str] = []

        def token_reader(prompt: str) -> str:
            prompts.append(prompt)
            capability = launcher.CAPABILITIES[len(prompts) - 1]
            return fake.tokens[capability]

        planned = launcher.bootstrap(
            config,
            apply=False,
            attended=False,
            confirmation=None,
            runner=fake,
        )
        assert planned["result"] == "ready_for_attended_three_paste_bootstrap"
        assert fake.calls == []
        completed = launcher.bootstrap(
            config,
            apply=True,
            attended=True,
            confirmation=launcher.BOOTSTRAP_CONFIRMATION,
            runner=fake,
            token_reader=token_reader,
        )
        assert completed["result"] == "bootstrapped"
        assert completed["desktop_authorization_batches"] == 0
        assert completed["manual_secret_entries"] == 3
        assert completed["values_exposed"] is False
        assert len([call for call in fake.calls if call[1:3] == ["vault", "list"]]) == 3
        assert prompts == [
            f"{capability} service-account token: " for capability in launcher.CAPABILITIES
        ]
        for capability, token in fake.tokens.items():
            path = Path(config["onepassword"]["domains"][capability]["bootstrap_file"])
            assert path.stat().st_mode & 0o777 == 0o400
            assert path.read_text(encoding="utf-8") == token + "\n"
        serialized = json.dumps(completed, sort_keys=True)
        assert not any(token in serialized for token in fake.tokens.values())


def test_bootstrap_rejects_wrong_service_account_scope_before_writing() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        config = config_fixture(root)
        fake = FakeServiceAccounts(config, wrong_scope=True)
        values = iter(fake.tokens[capability] for capability in launcher.CAPABILITIES)
        try:
            launcher.bootstrap(
                config,
                apply=True,
                attended=True,
                confirmation=launcher.BOOTSTRAP_CONFIRMATION,
                runner=fake,
                token_reader=lambda _prompt: next(values),
            )
        except launcher.LauncherError as exc:
            assert "one exact vault" in str(exc)
        else:
            raise AssertionError("wrong 1Password vault scope was accepted")
        assert not any(
            Path(domain["bootstrap_file"]).exists()
            for domain in config["onepassword"]["domains"].values()
        )


def test_supervisor_passes_values_only_by_descriptor_without_root_or_op_state() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        config = config_fixture(root)
        selected_config = root / "selected-userland-config.json"
        selected_config.write_bytes(userland.DEFAULT_CONFIG.read_bytes())
        calls: list[FakeProcess] = []
        saved_status = launcher.bootstrap_status
        saved_resolve = launcher.resolve_runtime_value

        def fake_popen(command: list[str], **kwargs: Any) -> FakeProcess:
            process = FakeProcess(command, kwargs)
            calls.append(process)
            return process

        values = {
            launcher.CAPABILITIES[0]: b"fixture-webhook-value",
            launcher.CAPABILITIES[1]: (b'-----BEGIN ' + b'PRIVATE KEY-----\nfixture\n-----END PRIVATE KEY-----'),
            launcher.CAPABILITIES[2]: b"fixture-cloudflare-token",
        }
        try:
            launcher.bootstrap_status = lambda _config: {"result": "ready"}  # type: ignore[assignment]
            launcher.resolve_runtime_value = (  # type: ignore[assignment]
                lambda _config, capability: values[capability]
            )
            assert launcher.start(
                config,
                config_path=selected_config,
                popen=fake_popen,
            ) == 0
        finally:
            launcher.bootstrap_status = saved_status
            launcher.resolve_runtime_value = saved_resolve
        assert len(calls) == 2
        conductor, tunnel = calls
        assert conductor.command[-2:] == ["serve", "--apply"]
        assert conductor.command[conductor.command.index("--config") + 1] == str(
            selected_config
        )
        assert str(userland.DEFAULT_CONFIG) not in conductor.command
        assert tunnel.command[1:5] == ["tunnel", "--no-autoupdate", "run", "--token-file"]
        assert tunnel.command[-1].startswith("/dev/fd/")
        serialized_commands = json.dumps([process.command for process in calls])
        serialized_environments = json.dumps([process.kwargs["env"] for process in calls])
        assert not any(value.decode("utf-8") in serialized_commands for value in values.values())
        assert not any(value.decode("utf-8") in serialized_environments for value in values.values())
        assert not any(
            key.startswith("OP_")
            for process in calls
            for key in process.kwargs["env"]
        )
        assert "sudo" not in serialized_commands


def test_launcher_main_passes_the_exact_selected_config_to_start() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        selected_config = Path(temporary) / "selected-userland-config.json"
        selected_config.write_bytes(userland.DEFAULT_CONFIG.read_bytes())
        expected_config = {"fixture": "selected"}
        observed: dict[str, Any] = {}
        saved_load = launcher.userland.load_config
        saved_start = launcher.start

        def fake_load(path: Path) -> dict[str, Any]:
            observed["loaded_path"] = path
            return expected_config

        def fake_start(config: dict[str, Any], *, config_path: Path) -> int:
            observed["started_config"] = config
            observed["started_path"] = config_path
            return 0

        try:
            launcher.userland.load_config = fake_load  # type: ignore[assignment]
            launcher.start = fake_start  # type: ignore[assignment]
            assert launcher.main(
                ["--config", str(selected_config), "start", "--apply"]
            ) == 0
        finally:
            launcher.userland.load_config = saved_load
            launcher.start = saved_start

        assert observed == {
            "loaded_path": selected_config.resolve(),
            "started_config": expected_config,
            "started_path": selected_config.resolve(),
        }


def test_openclaw_human_gate_is_terminal_without_dispatching_clawsweeper() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        config = config_fixture(root)
        pr = 71
        ingress(config, "pull_request", "human-pr", pr_payload(pr))
        ingress(config, "workflow_run", "human-ci", ci_payload(pr, 1071))
        queued = action(config, pr, "openclaw.enqueue")
        mark_dispatched(config, queued["action_id"])
        payload = json.loads(queued["payload_json"])
        proof = Path(config["paths"]["proof_root"]) / "openclaw-human.md"
        proof.write_text("bounded human gate proof\n", encoding="utf-8")
        artifact = {
            "schema": runtime.OPENCLAW_ARTIFACT_SCHEMA,
            "request_id": payload["queue_request_id"],
            "operator_id": payload["operator_id"],
            "status": "needs-human",
            "repository": "dinkuskit/blocks",
            "pr_number": pr,
            "base_sha": BASE,
            "head_sha": HEAD,
            "review_epoch": 0,
            "review_clean": True,
            "review_finding_count": 0,
            "reviewer_actor": "spark-openclaw",
            "proof_ref": str(proof),
            "proof_sha256": hashlib.sha256(proof.read_bytes()).hexdigest(),
        }
        artifact_path = root / "human.terminal.json"
        artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
        result = runtime.bridge_openclaw(config, artifact_path)
        assert result["state"] == "waiting_human"
        assert current(config, pr)["state"] == "waiting_human"
        try:
            core.ingest_internal_event(
                config_path=Path(config["core_config"]),
                state_root=Path(config["paths"]["state_root"]),
                event_payload={
                    "schema": core.INTERNAL_EVENT_SCHEMA,
                    "event_id": "userland-openclaw-human-gate-adjudication",
                    "type": "adjudication.completed",
                    "repository": "dinkuskit/blocks",
                    "pr_number": pr,
                    "base_sha": BASE,
                    "head_sha": HEAD,
                    "request_id": payload["queue_request_id"],
                    "rail": "openclaw",
                    "classifications": ["defer"],
                    "reviewer_actor": "spark-openclaw",
                    "proof_ref": "proof/adjudication/openclaw-human-gate/ADJUDICATION.md",
                    "review_epoch": 0,
                },
            )
        except core.ContractError as exc:
            assert "adjudication is invalid from state waiting_human" in str(exc)
        else:
            raise AssertionError("OpenClaw human_gate must remain fail-closed")
        assert current(config, pr)["state"] == "waiting_human"
        connection = core.open_database(Path(config["paths"]["state_root"]))
        try:
            assert connection.execute(
                "SELECT COUNT(*) FROM actions WHERE pr_number = ? AND kind = 'clawsweeper.dispatch'",
                (pr,),
            ).fetchone()[0] == 0
        finally:
            connection.close()


def test_ci_failure_alerts_without_starting_either_review_rail() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        config = config_fixture(Path(temporary))
        pr = 77
        ingress(config, "pull_request", "failed-ci-pr", pr_payload(pr))
        ingress(
            config,
            "workflow_run",
            "failed-ci-run",
            ci_payload(pr, 1077, conclusion="failure"),
        )
        assert current(config, pr)["state"] == "ci_failed"
        connection = core.open_database(Path(config["paths"]["state_root"]))
        try:
            assert connection.execute(
                "SELECT COUNT(*) FROM actions WHERE pr_number = ?", (pr,)
            ).fetchone()[0] == 0
        finally:
            connection.close()
        notifier = FakeNotifier()
        userland.deliver_notifications(config, notifier, dry_run=False)
        row = current(config, pr)
        assert_exact_terminal_messages(notifier, concise_blocked(pr, row["blocker"]))


def test_real_spark_receipt_shape_bridges_nonzero_human_gate() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        config = config_fixture(root)
        pr = 75
        ingress(config, "pull_request", "spark-human-pr", pr_payload(pr))
        ingress(config, "workflow_run", "spark-human-ci", ci_payload(pr, 1075))
        queued = action(config, pr, "openclaw.enqueue")
        mark_dispatched(config, queued["action_id"])
        request_id = json.loads(queued["payload_json"])["queue_request_id"]
        run = openclaw_run_fixture(root, request_id)
        status = json.loads((run / "REQUEST_STATUS.json").read_text(encoding="utf-8"))
        status.update(
            {
                "status": "needs-human",
                "review_clean": None,
                "review_finding_count": None,
            }
        )
        (run / "REQUEST_STATUS.json").write_text(
            json.dumps(status) + "\n", encoding="utf-8"
        )
        result = userland.collect_openclaw_terminals(
            config,
            dry_run=False,
            runner=SparkStatusRunner(run, result="needs-human"),
        )
        assert result == [{"request_id": request_id, "result": "terminal_materialized"}]
        runtime.drain_bridge_inboxes(config)
        assert current(config, pr)["state"] == "waiting_human"
        connection = core.open_database(Path(config["paths"]["state_root"]))
        try:
            assert connection.execute(
                "SELECT COUNT(*) FROM actions WHERE pr_number = ? AND kind = 'clawsweeper.dispatch'",
                (pr,),
            ).fetchone()[0] == 0
        finally:
            connection.close()


def test_legacy_openclaw_terminal_without_review_policy_does_not_require_applied_p3() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        config = config_fixture(root)
        assert not config.get("review_policy")
        pr = 76
        ingress(config, "pull_request", "legacy-openclaw-pr", pr_payload(pr))
        ingress(config, "workflow_run", "legacy-openclaw-ci", ci_payload(pr, 1076))
        queued = action(config, pr, "openclaw.enqueue")
        mark_dispatched(config, queued["action_id"])
        request_id = json.loads(queued["payload_json"])["queue_request_id"]
        run = openclaw_run_fixture(root, request_id)
        status = json.loads((run / "REQUEST_STATUS.json").read_text(encoding="utf-8"))
        assert "native_max_priority" not in status
        assert "applied_max_priority" not in status
        assert "exact_tuple_qualified" not in status
        assert "review_scope" not in status
        result = userland.collect_openclaw_terminals(
            config,
            dry_run=False,
            runner=SparkStatusRunner(run),
        )
        assert result == [{"request_id": request_id, "result": "terminal_materialized"}]
        artifact = Path(config["spark"]["terminal_inbox"]) / f"{request_id}.terminal.json"
        assert artifact.is_file()
        value = json.loads(artifact.read_text(encoding="utf-8"))
        assert "native_max_priority" not in value
        assert "applied_max_priority" not in value
        assert "exact_tuple_qualified" not in value
        runtime.drain_bridge_inboxes(config)
        assert current(config, pr)["state"] == "clawsweeper_queued"


def test_exact_artifacts_drive_ready_notification_once_without_merge() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        config = config_fixture(root)
        pr = 72
        run_id = 2072
        prepare_openclaw(config, root, pr)
        prepare_clawsweeper_run(config, pr, run_id)
        client = FakeGitHub(run_id, claw_bundle(run_id=run_id, pr=pr))
        collected = userland.collect_clawsweeper_terminals(
            config, client, dry_run=False
        )
        assert collected == [
            {
                "workflow_run_id": run_id,
                "result": "terminal_materialized",
                "verdict": "clean",
                "ready_qualified": True,
            }
        ]
        runtime.drain_bridge_inboxes(config)
        assert current(config, pr)["state"] == "ready_for_human_merge"
        notifier = FakeNotifier()
        first = userland.deliver_notifications(config, notifier, dry_run=False)
        assert_exact_terminal_messages(notifier, concise_ready(pr))
        assert len(first["deliveries"]) == 3
        second = userland.deliver_notifications(config, notifier, dry_run=False)
        assert second["deliveries"] == []
        assert len(notifier.sent) == 3
        assert current(config, pr)["state"] == "ready_for_human_merge"


def test_clawsweeper_finding_waits_for_adjudication_without_notification() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        config = config_fixture(root)
        pr = 73
        run_id = 2073
        prepare_openclaw(config, root, pr)
        prepare_clawsweeper_run(config, pr, run_id)
        client = FakeGitHub(
            run_id,
            claw_bundle(run_id=run_id, pr=pr, finding=True),
        )
        collected = userland.collect_clawsweeper_terminals(
            config, client, dry_run=False
        )
        assert collected[0]["verdict"] == "findings"
        runtime.drain_bridge_inboxes(config)
        assert current(config, pr)["state"] == "awaiting_adjudication"
        notifier = FakeNotifier()
        delivered = userland.deliver_notifications(config, notifier, dry_run=False)
        assert notifier.sent == []
        assert delivered["deliveries"] == []


def test_first_round_openclaw_findings_and_repair_required_are_silent() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        config = config_fixture(Path(temporary))
        pr = 76
        ingress(config, "pull_request", "silent-openclaw-pr", pr_payload(pr))
        ingress(config, "workflow_run", "silent-openclaw-ci", ci_payload(pr, 1076))
        connection = core.open_database(Path(config["paths"]["state_root"]))
        try:
            connection.execute(
                """
                UPDATE heads
                SET state = 'awaiting_adjudication', rail = 'openclaw',
                    repair_cycle = 0, blocker = 'OpenClaw findings require bounded adjudication'
                WHERE pr_number = ? AND is_current = 1
                """,
                (pr,),
            )
            connection.commit()
        finally:
            connection.close()
        notifier = FakeNotifier()
        first = userland.deliver_notifications(config, notifier, dry_run=False)
        assert current(config, pr)["state"] == "awaiting_adjudication"
        assert notifier.sent == []
        assert first["deliveries"] == []
        connection = core.open_database(Path(config["paths"]["state_root"]))
        try:
            connection.execute(
                """
                UPDATE heads
                SET state = 'repair_required', repair_cycle = 1,
                    blocker = 'accepted required fix must be patched by the single mutation owner'
                WHERE pr_number = ? AND is_current = 1
                """,
                (pr,),
            )
            connection.commit()
        finally:
            connection.close()
        second = userland.deliver_notifications(config, notifier, dry_run=False)
        assert current(config, pr)["state"] == "repair_required"
        assert notifier.sent == []
        assert second["deliveries"] == []


def test_waiting_human_missing_rail_queues_blocked_notification_once() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        config = config_fixture(Path(temporary))
        pr = 85
        ingress(config, "pull_request", "missing-rail-pr", pr_payload(pr))
        set_head(config, pr, state="waiting_human", rail=None, blocker="stale missing rail")
        created = userland.queue_notifications(config)
        assert created == 3
        outcomes = queued_notification_outcomes(config)
        assert outcomes
        for item in outcomes:
            assert item["route"] == "fail_closed"
            assert item["reason"] == "unknown_rail_result_fail_closed"
            assert item["notification"]["eligibility"] == "blocked"
        notifier = FakeNotifier()
        delivered = userland.deliver_notifications(config, notifier, dry_run=False)
        assert len(delivered["deliveries"]) == 3
        assert_exact_terminal_messages(notifier, concise_blocked(pr, "unknown rail result"))
        again = userland.deliver_notifications(config, FakeNotifier(), dry_run=False)
        assert again["deliveries"] == []
        assert len(_notification_rows(config)) == 3


def test_unknown_head_outcome_fail_closed_routes_blocked_notification() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        config = config_fixture(Path(temporary))
        pr = 79
        ingress(config, "pull_request", "unknown-outcome-pr", pr_payload(pr))
        connection = core.open_database(Path(config["paths"]["state_root"]))
        try:
            connection.execute(
                "UPDATE heads SET state = 'mystery_state' WHERE pr_number = ? AND is_current = 1",
                (pr,),
            )
            connection.commit()
            row = connection.execute(
                "SELECT * FROM heads WHERE pr_number = ? AND is_current = 1",
                (pr,),
            ).fetchone()
            decision = userland.orchestration.decide_orchestration_outcome(
                userland.orchestration.outcome_from_review_row(
                    row,
                    enrollment=userland.orchestration.resolve_trusted_enrollment(config),
                )
            )
        finally:
            connection.close()
        assert decision["route"] == "fail_closed"
        assert decision["review_dispatch"] is False
        assert decision["legacy_dispatch"] is False
        assert decision["reason"] == "unknown_state_fail_closed"
        notifier = FakeNotifier()
        delivered = userland.deliver_notifications(config, notifier, dry_run=False)
        assert len(delivered["deliveries"]) == 3
        assert_exact_terminal_messages(notifier, concise_blocked(pr, "unknown state"))


def test_broken_enrollment_fail_closed_routes_blocked_notification() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        config = config_fixture(Path(temporary))
        pr = 80
        ingress(config, "pull_request", "broken-enrollment-pr", pr_payload(pr))
        enrollment = {"review_conductor": "broken", "legacy_xapi": "absent"}
        notifier = FakeNotifier()
        delivered = userland.deliver_notifications(
            config, notifier, dry_run=False, enrollment=enrollment
        )
        assert current(config, pr)["state"] == "ci_running"
        assert len(delivered["deliveries"]) == 3
        assert_exact_terminal_messages(
            notifier, concise_blocked(pr, "ambiguous or broken enrollment")
        )
        connection = core.open_database(Path(config["paths"]["state_root"]))
        try:
            payloads = [
                json.loads(row["payload_json"])
                for row in connection.execute(
                    "SELECT payload_json FROM notification_deliveries ORDER BY channel"
                )
            ]
        finally:
            connection.close()
        assert payloads
        for payload in payloads:
            outcome = payload["orchestration_outcome"]
            assert outcome["route"] == "fail_closed"
            assert outcome["reason"] == "ambiguous_or_broken_enrollment"
            assert outcome["notification"]["eligibility"] == "blocked"


def test_closed_heads_reject_direct_and_service_loop_dispatch() -> None:
    for state in ("closed", "closed_merged"):
        with tempfile.TemporaryDirectory() as temporary:
            config = config_fixture(Path(temporary))
            pr = 81 if state == "closed" else 82
            ingress(config, "pull_request", f"{state}-pr", pr_payload(pr))
            ingress(config, "workflow_run", f"{state}-ci", ci_payload(pr, 1080 + pr))
            assert action(config, pr, "openclaw.enqueue")["status"] == "pending"
            set_head(config, pr, state=state, blocker="stale closed blocker text")
            decision = userland.orchestration.decide_orchestration_outcome(
                userland.orchestration.outcome_from_review_row(
                    current(config, pr),
                    enrollment=userland.orchestration.resolve_trusted_enrollment(config),
                )
            )
            assert decision["review_dispatch"] is False
            assert decision["legacy_dispatch"] is False
            assert decision["notification"]["eligibility"] == "silent"
            assert decision["reason"] == "terminal_closed_non_dispatchable"
            drained = runtime.drain_actions(config, None, dry_run=False)
            assert drained["actions"] == []
            assert action(config, pr, "openclaw.enqueue")["status"] == "pending"
            notifier = FakeNotifier()
            tick = userland.run_tick(config, None, notifier, dry_run=True)
            assert notifier.sent == []
            assert queued_notification_outcomes(config) == []
            assert tick["notifications"]["deliveries"] == []
            assert current(config, pr)["state"] == state


def test_closed_heads_stay_silent_when_trusted_enrollment_is_broken() -> None:
    for state in ("closed", "closed_merged"):
        with tempfile.TemporaryDirectory() as temporary:
            config = config_fixture(Path(temporary))
            pr = 83 if state == "closed" else 84
            ingress(config, "pull_request", f"{state}-broken-pr", pr_payload(pr))
            ingress(config, "workflow_run", f"{state}-broken-ci", ci_payload(pr, 1180 + pr))
            assert action(config, pr, "openclaw.enqueue")["status"] == "pending"
            set_trusted_enrollment(config, "broken", "absent")
            set_head(config, pr, state=state, blocker="stale closed blocker text")
            decision = userland.orchestration.decide_orchestration_outcome(
                userland.orchestration.outcome_from_review_row(
                    current(config, pr),
                    enrollment=userland.orchestration.resolve_trusted_enrollment(config),
                )
            )
            assert decision["route"] == "fail_closed"
            assert decision["review_dispatch"] is False
            assert decision["legacy_dispatch"] is False
            assert decision["notification"]["eligibility"] == "silent"
            assert decision["reason"] == "terminal_closed_non_dispatchable"
            notifier = FakeNotifier()
            tick = userland.run_tick(config, None, notifier, dry_run=True)
            assert tick["worker"]["result"] == "skipped"
            assert tick["hydration"] == []
            assert tick["openclaw"] == []
            assert tick["clawsweeper"] == []
            assert tick["bridges_before"]["result"] == "skipped"
            assert tick["projection"]["result"] == "skipped"
            assert notifier.sent == []
            assert queued_notification_outcomes(config) == []
            delivered = userland.deliver_notifications(config, notifier, dry_run=False)
            assert delivered["deliveries"] == []
            assert notifier.sent == []
            assert action(config, pr, "openclaw.enqueue")["status"] == "pending"


def test_run_tick_suppresses_review_stages_unless_conductor_enrolled() -> None:
    cases = (
        ("present", "absent", "review_conductor", True, False),
        ("present", "present", "review_conductor", True, False),
        ("absent", "present", "legacy_xapi", False, False),
        ("absent", "absent", "none", False, False),
        ("broken", "absent", "fail_closed", False, True),
    )
    for index, (
        review_status,
        legacy_status,
        route,
        runs_stages,
        notifies,
    ) in enumerate(cases):
        with tempfile.TemporaryDirectory() as temporary:
            config = config_fixture(Path(temporary))
            pr = 100 + index
            ingress(config, "pull_request", f"stage-{pr}", pr_payload(pr))
            ingress(config, "workflow_run", f"stage-ci-{pr}", ci_payload(pr, 2100 + pr))
            queued = action(config, pr, "openclaw.enqueue")
            assert queued["status"] == "pending"
            assert current(config, pr)["state"] == "openclaw_queued"
            set_trusted_enrollment(config, review_status, legacy_status)
            trusted = userland.orchestration.resolve_trusted_enrollment(config)
            assert trusted == {
                "review_conductor": review_status,
                "legacy_xapi": legacy_status,
            }
            assert userland.orchestration.enrollment_route(trusted)[0] == route
            notifier = FakeNotifier()
            tick = userland.run_tick(config, None, notifier, dry_run=True)
            assert action(config, pr, "openclaw.enqueue")["status"] == "pending"
            if runs_stages:
                assert tick["hydration"]
                assert tick["worker"]["result"] == "planned"
                assert tick["worker"]["actions"]
                assert tick["bridges_before"]["result"] == "planned"
            else:
                assert tick["hydration"] == []
                assert tick["worker"]["result"] == "skipped"
                assert tick["worker"]["actions"] == []
                assert tick["openclaw"] == []
                assert tick["clawsweeper"] == []
                assert tick["bridges_before"]["result"] == "skipped"
                assert tick["projection"]["result"] == "skipped"
            outcomes = queued_notification_outcomes(config)
            if notifies:
                assert outcomes
                for outcome in outcomes:
                    assert outcome["route"] == "fail_closed"
                    assert outcome["notification"]["eligibility"] == "blocked"
                    assert outcome["reason"] == "ambiguous_or_broken_enrollment"
            else:
                assert outcomes == []
                assert all(
                    item["result"] != "planned" or route == "review_conductor"
                    for item in tick["notifications"]["deliveries"]
                )


def test_pending_notifications_are_revalidated_before_send() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        config = config_fixture(Path(temporary))
        pr = 110
        ingress(config, "pull_request", "stale-notify-pr", pr_payload(pr))
        ingress(
            config,
            "workflow_run",
            "stale-notify-ci",
            ci_payload(pr, 2110, conclusion="failure"),
        )
        assert current(config, pr)["state"] == "ci_failed"
        first = userland.deliver_notifications(config, UnavailableNotifier(), dry_run=False)
        assert [item["result"] for item in first["deliveries"]] == [
            "not_ready",
            "not_ready",
            "not_ready",
        ]
        assert notification_statuses(config) == [
            ("discord", "pending"),
            ("openclaw_context", "pending"),
            ("signal", "pending"),
        ]

        set_head(config, pr, state="closed", blocker="closed after queued notify")
        closed = userland.deliver_notifications(config, FakeNotifier(), dry_run=False)
        assert [item["result"] for item in closed["deliveries"]] == [
            "retired",
            "retired",
            "retired",
        ]
        assert notification_statuses(config) == [
            ("discord", "retired"),
            ("openclaw_context", "retired"),
            ("signal", "retired"),
        ]

    with tempfile.TemporaryDirectory() as temporary:
        config = config_fixture(Path(temporary))
        pr = 111
        ingress(config, "pull_request", "enroll-change-pr", pr_payload(pr))
        ingress(
            config,
            "workflow_run",
            "enroll-change-ci",
            ci_payload(pr, 2111, conclusion="failure"),
        )
        userland.deliver_notifications(config, UnavailableNotifier(), dry_run=False)
        set_trusted_enrollment(config, "absent", "present")
        changed = userland.deliver_notifications(config, FakeNotifier(), dry_run=False)
        assert [item["result"] for item in changed["deliveries"]] == [
            "retired",
            "retired",
            "retired",
        ]
        assert all(status == "retired" for _channel, status in notification_statuses(config))

    with tempfile.TemporaryDirectory() as temporary:
        config = config_fixture(Path(temporary))
        pr = 112
        ingress(config, "pull_request", "supersede-pr", pr_payload(pr))
        ingress(
            config,
            "workflow_run",
            "supersede-ci",
            ci_payload(pr, 2112, conclusion="failure"),
        )
        userland.deliver_notifications(config, UnavailableNotifier(), dry_run=False)
        connection = core.open_database(Path(config["paths"]["state_root"]))
        try:
            connection.execute(
                "UPDATE heads SET head_sha = ? WHERE pr_number = ? AND is_current = 1",
                ("3" * 40, pr),
            )
            connection.commit()
        finally:
            connection.close()
        notifier = FakeNotifier()
        superseded = userland.deliver_notifications(config, notifier, dry_run=False)
        results = {item["result"] for item in superseded["deliveries"]}
        assert "retired" in results
        assert "sent" in results
        assert notification_statuses(config).count(("discord", "retired")) == 1
        assert notification_statuses(config).count(("discord", "sent")) == 1
        assert_exact_terminal_messages(notifier, concise_blocked(pr, current(config, pr)["blocker"]))

    with tempfile.TemporaryDirectory() as temporary:
        config = config_fixture(Path(temporary))
        pr = 113
        ingress(config, "pull_request", "fail-closed-keep-pr", pr_payload(pr))
        set_head(config, pr, state="mystery_state", blocker="stale persisted CI text")
        userland.deliver_notifications(config, UnavailableNotifier(), dry_run=False)
        assert all(status == "pending" for _channel, status in notification_statuses(config))
        notifier = FakeNotifier()
        kept = userland.deliver_notifications(config, notifier, dry_run=False)
        assert [item["result"] for item in kept["deliveries"]] == ["sent", "sent", "sent"]
        assert all(status == "sent" for _channel, status in notification_statuses(config))
        assert_exact_terminal_messages(notifier, concise_blocked(pr, "unknown state"))

    with tempfile.TemporaryDirectory() as temporary:
        config = config_fixture(Path(temporary))
        pr = 114
        ingress(config, "pull_request", "stale-ready-pr", pr_payload(pr))
        ingress(
            config,
            "workflow_run",
            "stale-ready-ci",
            ci_payload(pr, 2114, conclusion="failure"),
        )
        row = current(config, pr)
        event_key = "a" * 64
        payload = {
            "schema": userland.NOTIFICATION_SCHEMA,
            "event_key": event_key,
            "repository": row["repository"],
            "pr_number": pr,
            "base_sha": row["base_sha"],
            "head_sha": row["head_sha"],
            "review_epoch": row["review_epoch"],
            "state": "ready_for_human_merge",
            "repair_cycle": row["repair_cycle"],
            "message": concise_ready(pr),
            "merge_authorized": False,
            "orchestration_outcome": {
                "schema": userland.orchestration.OUTCOME_SCHEMA,
                "route": "review_conductor",
                "notification": {
                    "eligibility": "merge_ready",
                    "kind": "merge_ready",
                    "channels": list(userland.orchestration.NOTIFY_CHANNELS),
                },
                "reason": "both_rails_effectively_clean",
            },
        }
        connection = core.open_database(Path(config["paths"]["state_root"]))
        try:
            userland.ensure_userland_tables(connection)
            for channel in userland.orchestration.NOTIFY_CHANNELS:
                connection.execute(
                    """
                    INSERT INTO notification_deliveries(
                      event_key, channel, repository, pr_number, base_sha, head_sha,
                      review_epoch, state, payload_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event_key,
                        channel,
                        row["repository"],
                        pr,
                        row["base_sha"],
                        row["head_sha"],
                        row["review_epoch"],
                        "ready_for_human_merge",
                        core.canonical_json(payload),
                        core.utc_now(),
                        core.utc_now(),
                    ),
                )
            connection.commit()
        finally:
            connection.close()
        notifier = FakeNotifier()
        stale_ready = userland.deliver_notifications(config, notifier, dry_run=False)
        assert {item["result"] for item in stale_ready["deliveries"]} == {"retired", "sent"}
        assert notification_statuses(config).count(("discord", "retired")) == 1
        assert notification_statuses(config).count(("discord", "sent")) == 1
        assert_exact_terminal_messages(notifier, concise_blocked(pr, current(config, pr)["blocker"]))
        for _channel, message in notifier.sent:
            assert "ready to merge" not in message


def test_stale_decision_identity_retires_and_new_blocked_delivers_once() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        config = config_fixture(Path(temporary))
        pr = 115
        ingress(config, "pull_request", "decision-id-pr", pr_payload(pr))
        ingress(
            config,
            "workflow_run",
            "decision-id-ci",
            ci_payload(pr, 2115, conclusion="failure"),
        )
        first = userland.deliver_notifications(config, UnavailableNotifier(), dry_run=False)
        assert [item["result"] for item in first["deliveries"]] == [
            "not_ready",
            "not_ready",
            "not_ready",
        ]
        original_rows = _notification_rows(config)
        assert len(original_rows) == 3
        original_keys = {row[0] for row in original_rows}
        assert len(original_keys) == 1
        set_trusted_enrollment(config, "broken", "absent")
        notifier = FakeNotifier()
        changed = userland.deliver_notifications(config, notifier, dry_run=False)
        results = [item["result"] for item in changed["deliveries"]]
        assert results.count("retired") == 3
        assert results.count("sent") == 3
        statuses = notification_statuses(config)
        assert statuses.count(("discord", "retired")) == 1
        assert statuses.count(("discord", "sent")) == 1
        assert statuses.count(("openclaw_context", "retired")) == 1
        assert statuses.count(("openclaw_context", "sent")) == 1
        assert statuses.count(("signal", "retired")) == 1
        assert statuses.count(("signal", "sent")) == 1
        current_keys = {row[0] for row in _notification_rows(config) if row[1] == "sent"}
        assert current_keys.isdisjoint(original_keys)
        assert_exact_terminal_messages(
            notifier, concise_blocked(pr, "ambiguous or broken enrollment")
        )
        sent_outcomes = [
            json.loads(row[2])["orchestration_outcome"]
            for row in _notification_rows(config)
            if row[1] == "sent"
        ]
        assert sent_outcomes
        for item in sent_outcomes:
            assert item["route"] == "fail_closed"
            assert item["reason"] == "ambiguous_or_broken_enrollment"
            assert item["notification"]["eligibility"] == "blocked"
        second = FakeNotifier()
        again = userland.deliver_notifications(config, second, dry_run=False)
        assert again["deliveries"] == []
        assert second.sent == []
        assert {row[1] for row in _notification_rows(config)} == {"retired", "sent"}
        assert len(_notification_rows(config)) == 6


def test_webhook_state_change_between_claim_and_send_retires_stale_notification() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        config = config_fixture(Path(temporary))
        pr = 117
        ingress(config, "pull_request", "race-notify-pr", pr_payload(pr))
        ingress(
            config,
            "workflow_run",
            "race-notify-ci",
            ci_payload(pr, 2117, conclusion="failure"),
        )
        first = userland.deliver_notifications(config, UnavailableNotifier(), dry_run=False)
        assert [item["result"] for item in first["deliveries"]] == [
            "not_ready",
            "not_ready",
            "not_ready",
        ]
        assert current(config, pr)["state"] == "ci_failed"

        class RacingClient:
            def assert_authority(self, operation):
                set_head(
                    config,
                    pr,
                    state="openclaw_queued",
                    rail="openclaw",
                    blocker=None,
                )

        notifier = FakeNotifier()
        raced = userland.deliver_notifications(
            config, notifier, dry_run=False, authority_client=RacingClient()
        )
        assert [item["result"] for item in raced["deliveries"]] == [
            "retired",
            "retired",
            "retired",
        ]
        assert notifier.sent == []
        assert notification_statuses(config) == [
            ("discord", "retired"),
            ("openclaw_context", "retired"),
            ("signal", "retired"),
        ]
        assert current(config, pr)["state"] == "openclaw_queued"

    with tempfile.TemporaryDirectory() as temporary:
        config = config_fixture(Path(temporary))
        pr = 118
        ingress(config, "pull_request", "same-decision-pr", pr_payload(pr))
        ingress(
            config,
            "workflow_run",
            "same-decision-ci",
            ci_payload(pr, 2118, conclusion="failure"),
        )
        userland.deliver_notifications(config, UnavailableNotifier(), dry_run=False)
        notifier = FakeNotifier()
        delivered = userland.deliver_notifications(config, notifier, dry_run=False)
        assert [item["result"] for item in delivered["deliveries"]] == [
            "sent",
            "sent",
            "sent",
        ]
        assert_exact_terminal_messages(notifier, concise_blocked(pr, current(config, pr)["blocker"]))
        second = FakeNotifier()
        again = userland.deliver_notifications(config, second, dry_run=False)
        assert again["deliveries"] == []
        assert second.sent == []
        assert {status for _channel, status in notification_statuses(config)} == {"sent"}


def test_webhook_state_change_after_final_predicate_retires_stale_notification() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        config = config_fixture(Path(temporary))
        pr = 119
        ingress(config, "pull_request", "after-predicate-pr", pr_payload(pr))
        ingress(
            config,
            "workflow_run",
            "after-predicate-ci",
            ci_payload(pr, 2119, conclusion="failure"),
        )
        first = userland.deliver_notifications(config, UnavailableNotifier(), dry_run=False)
        assert [item["result"] for item in first["deliveries"]] == [
            "not_ready",
            "not_ready",
            "not_ready",
        ]
        original = userland.claimed_notification_still_current
        seen = {"count": 0}

        def after_predicate(connection, row, trusted_enrollment, payload=None):
            still = original(connection, row, trusted_enrollment, payload)
            seen["count"] += 1
            if still and seen["count"] == 1:
                set_head(
                    config,
                    pr,
                    state="openclaw_queued",
                    rail="openclaw",
                    blocker=None,
                )
            return still

        notifier = FakeNotifier()
        userland.claimed_notification_still_current = after_predicate  # type: ignore[method-assign]
        try:
            raced = userland.deliver_notifications(config, notifier, dry_run=False)
        finally:
            userland.claimed_notification_still_current = original
        assert seen["count"] >= 2
        assert [item["result"] for item in raced["deliveries"]] == [
            "retired",
            "retired",
            "retired",
        ]
        assert notifier.sent == []
        assert notification_statuses(config) == [
            ("discord", "retired"),
            ("openclaw_context", "retired"),
            ("signal", "retired"),
        ]
        assert current(config, pr)["state"] == "openclaw_queued"

    with tempfile.TemporaryDirectory() as temporary:
        config = config_fixture(Path(temporary))
        pr = 120
        ingress(config, "pull_request", "same-after-pr", pr_payload(pr))
        ingress(
            config,
            "workflow_run",
            "same-after-ci",
            ci_payload(pr, 2120, conclusion="failure"),
        )
        userland.deliver_notifications(config, UnavailableNotifier(), dry_run=False)
        notifier = FakeNotifier()
        delivered = userland.deliver_notifications(config, notifier, dry_run=False)
        assert [item["result"] for item in delivered["deliveries"]] == [
            "sent",
            "sent",
            "sent",
        ]
        assert_exact_terminal_messages(notifier, concise_blocked(pr, current(config, pr)["blocker"]))
        second = FakeNotifier()
        again = userland.deliver_notifications(config, second, dry_run=False)
        assert again["deliveries"] == []
        assert second.sent == []
        assert {status for _channel, status in notification_statuses(config)} == {"sent"}


def test_persisted_string_and_malformed_quality_flags_fail_closed_on_the_queue() -> None:
    enrolled = {"review_conductor": "present", "legacy_xapi": "absent"}
    with tempfile.TemporaryDirectory() as temporary:
        config = config_fixture(Path(temporary))
        pr = 130
        ingress(config, "pull_request", "quality-false-pr", pr_payload(pr))
        set_head(config, pr, state="ready_for_human_merge", rail="clawsweeper")
        insert_quality(config, pr, "false")
        mapped = userland.orchestration.outcome_from_review_row(
            current(config, pr),
            {"ready_qualified": "false"},
            enrollment=enrolled,
        )
        assert mapped["ready_qualified"] is None
        assert mapped["openclaw_result"] == "unknown"
        decision = userland.orchestration.decide_orchestration_outcome(mapped)
        assert decision["route"] == "fail_closed"
        assert decision["merge_ready_eligible"] is False
        assert decision["notification"]["eligibility"] == "blocked"
        notifier = FakeNotifier()
        delivered = userland.deliver_notifications(config, notifier, dry_run=False)
        assert [item["result"] for item in delivered["deliveries"]] == [
            "sent",
            "sent",
            "sent",
        ]
        outcomes = queued_notification_outcomes(config)
        assert outcomes
        for item in outcomes:
            assert item["route"] == "fail_closed"
            assert item["notification"]["eligibility"] == "blocked"
            assert item["reason"] == "unknown_rail_result_fail_closed"
        for _channel, message in notifier.sent:
            assert "ready to merge" not in message
        assert_exact_terminal_messages(notifier, concise_blocked(pr, "unknown rail result"))

    with tempfile.TemporaryDirectory() as temporary:
        config = config_fixture(Path(temporary))
        pr = 131
        ingress(config, "pull_request", "quality-one-pr", pr_payload(pr))
        set_head(config, pr, state="ready_for_human_merge", rail="clawsweeper")
        insert_quality(config, pr, 1)
        notifier = FakeNotifier()
        delivered = userland.deliver_notifications(config, notifier, dry_run=False)
        assert [item["result"] for item in delivered["deliveries"]] == [
            "sent",
            "sent",
            "sent",
        ]
        outcomes = queued_notification_outcomes(config)
        assert outcomes
        for item in outcomes:
            assert item["route"] == "review_conductor"
            assert item["notification"]["eligibility"] == "merge_ready"
        assert_exact_terminal_messages(notifier, concise_ready(pr))

    with tempfile.TemporaryDirectory() as temporary:
        config = config_fixture(Path(temporary))
        pr = 132
        ingress(config, "pull_request", "quality-zero-pr", pr_payload(pr))
        set_head(config, pr, state="ready_for_human_merge", rail="clawsweeper")
        insert_quality(config, pr, 0)
        notifier = FakeNotifier()
        delivered = userland.deliver_notifications(config, notifier, dry_run=False)
        assert delivered["deliveries"] == []
        assert notifier.sent == []
        assert notification_statuses(config) == []


def test_enrollment_route_change_at_reserved_send_retires_stale_notification() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        config = config_fixture(Path(temporary))
        pr = 133
        ingress(config, "pull_request", "route-reserve-pr", pr_payload(pr))
        ingress(
            config,
            "workflow_run",
            "route-reserve-ci",
            ci_payload(pr, 2133, conclusion="failure"),
        )
        first = userland.deliver_notifications(config, UnavailableNotifier(), dry_run=False)
        assert [item["result"] for item in first["deliveries"]] == [
            "not_ready",
            "not_ready",
            "not_ready",
        ]
        original = userland.claimed_notification_still_current
        seen = {"count": 0}

        def after_predicate(connection, row, trusted_enrollment, payload=None):
            still = original(connection, row, trusted_enrollment, payload)
            seen["count"] += 1
            if still and seen["count"] == 1:
                set_trusted_enrollment(config, "broken", "absent")
            return still

        notifier = FakeNotifier()
        userland.claimed_notification_still_current = after_predicate  # type: ignore[method-assign]
        try:
            raced = userland.deliver_notifications(config, notifier, dry_run=False)
        finally:
            userland.claimed_notification_still_current = original
        assert seen["count"] >= 2
        assert [item["result"] for item in raced["deliveries"]] == [
            "retired",
            "retired",
            "retired",
        ]
        assert notifier.sent == []
        for _channel, status in notification_statuses(config):
            assert status == "retired"

    with tempfile.TemporaryDirectory() as temporary:
        config = config_fixture(Path(temporary))
        pr = 134
        ingress(config, "pull_request", "route-same-pr", pr_payload(pr))
        ingress(
            config,
            "workflow_run",
            "route-same-ci",
            ci_payload(pr, 2134, conclusion="failure"),
        )
        userland.deliver_notifications(config, UnavailableNotifier(), dry_run=False)
        notifier = FakeNotifier()
        delivered = userland.deliver_notifications(config, notifier, dry_run=False)
        assert [item["result"] for item in delivered["deliveries"]] == [
            "sent",
            "sent",
            "sent",
        ]
        assert_exact_terminal_messages(notifier, concise_blocked(pr, current(config, pr)["blocker"]))
        second = FakeNotifier()
        again = userland.deliver_notifications(config, second, dry_run=False)
        assert again["deliveries"] == []
        assert second.sent == []


def test_legacy_pending_rows_without_decision_identity_do_not_duplicate_delivery() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        config = config_fixture(Path(temporary))
        pr = 116
        ingress(config, "pull_request", "legacy-pending-pr", pr_payload(pr))
        ingress(
            config,
            "workflow_run",
            "legacy-pending-ci",
            ci_payload(pr, 2116, conclusion="failure"),
        )
        row = current(config, pr)
        assert row["state"] == "ci_failed"
        old_identity = (
            f"{row['repository']}|{row['pr_number']}|{row['base_sha']}|"
            f"{row['head_sha']}|{row['review_epoch']}|{row['state']}|{row['repair_cycle']}"
        )
        old_event_key = hashlib.sha256(old_identity.encode()).hexdigest()
        verbose = (
            f"dinkuskit/blocks#{pr} Review Conductor blocked. Repair cycle 0. "
            "exact-head CI failed. No merge was attempted."
        )
        payload = {
            "schema": userland.NOTIFICATION_SCHEMA,
            "event_key": old_event_key,
            "repository": row["repository"],
            "pr_number": pr,
            "base_sha": row["base_sha"],
            "head_sha": row["head_sha"],
            "review_epoch": row["review_epoch"],
            "state": row["state"],
            "repair_cycle": row["repair_cycle"],
            "message": verbose,
            "merge_authorized": False,
        }
        connection = core.open_database(Path(config["paths"]["state_root"]))
        try:
            userland.ensure_userland_tables(connection)
            for channel in userland.orchestration.NOTIFY_CHANNELS:
                connection.execute(
                    """
                    INSERT INTO notification_deliveries(
                      event_key, channel, repository, pr_number, base_sha, head_sha,
                      review_epoch, state, payload_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        old_event_key,
                        channel,
                        row["repository"],
                        pr,
                        row["base_sha"],
                        row["head_sha"],
                        row["review_epoch"],
                        row["state"],
                        core.canonical_json(payload),
                        core.utc_now(),
                        core.utc_now(),
                    ),
                )
            connection.commit()
        finally:
            connection.close()
        notifier = FakeNotifier()
        delivered = userland.deliver_notifications(config, notifier, dry_run=False)
        results = [item["result"] for item in delivered["deliveries"]]
        assert results.count("retired") == 3
        assert results.count("sent") == 3
        statuses = notification_statuses(config)
        assert statuses.count(("discord", "retired")) == 1
        assert statuses.count(("discord", "sent")) == 1
        assert statuses.count(("openclaw_context", "retired")) == 1
        assert statuses.count(("openclaw_context", "sent")) == 1
        assert statuses.count(("signal", "retired")) == 1
        assert statuses.count(("signal", "sent")) == 1
        retired_keys = {row[0] for row in _notification_rows(config) if row[1] == "retired"}
        sent_keys = {row[0] for row in _notification_rows(config) if row[1] == "sent"}
        assert retired_keys == {old_event_key}
        assert sent_keys
        assert sent_keys.isdisjoint(retired_keys)
        assert_exact_terminal_messages(notifier, concise_blocked(pr, current(config, pr)["blocker"]))
        for _channel, message in notifier.sent:
            assert verbose not in message
            for fragment in VERBOSE_TERMINAL_FRAGMENTS:
                assert fragment not in message
        sent_outcomes = [
            json.loads(row[2])["orchestration_outcome"]
            for row in _notification_rows(config)
            if row[1] == "sent"
        ]
        assert sent_outcomes
        for item in sent_outcomes:
            assert userland.pending_decision_matches_current(
                item,
                {
                    "schema": item["schema"],
                    "route": item["route"],
                    "reason": item["reason"],
                    "notification": item["notification"],
                },
            )
        second = FakeNotifier()
        again = userland.deliver_notifications(config, second, dry_run=False)
        assert again["deliveries"] == []
        assert second.sent == []
        assert len(_notification_rows(config)) == 6

    with tempfile.TemporaryDirectory() as temporary:
        config = config_fixture(Path(temporary))
        pr = 117
        ingress(config, "pull_request", "partial-identity-pr", pr_payload(pr))
        ingress(
            config,
            "workflow_run",
            "partial-identity-ci",
            ci_payload(pr, 2117, conclusion="failure"),
        )
        row = current(config, pr)
        event_key = "b" * 64
        payload = {
            "schema": userland.NOTIFICATION_SCHEMA,
            "event_key": event_key,
            "repository": row["repository"],
            "pr_number": pr,
            "base_sha": row["base_sha"],
            "head_sha": row["head_sha"],
            "review_epoch": row["review_epoch"],
            "state": row["state"],
            "repair_cycle": row["repair_cycle"],
            "message": concise_blocked(pr, current(config, pr)["blocker"]),
            "merge_authorized": False,
            "orchestration_outcome": {
                "route": "review_conductor",
                "reason": "human_action_required",
            },
        }
        connection = core.open_database(Path(config["paths"]["state_root"]))
        try:
            userland.ensure_userland_tables(connection)
            for channel in userland.orchestration.NOTIFY_CHANNELS:
                connection.execute(
                    """
                    INSERT INTO notification_deliveries(
                      event_key, channel, repository, pr_number, base_sha, head_sha,
                      review_epoch, state, payload_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event_key,
                        channel,
                        row["repository"],
                        pr,
                        row["base_sha"],
                        row["head_sha"],
                        row["review_epoch"],
                        row["state"],
                        core.canonical_json(payload),
                        core.utc_now(),
                        core.utc_now(),
                    ),
                )
            connection.commit()
        finally:
            connection.close()
        notifier = FakeNotifier()
        delivered = userland.deliver_notifications(config, notifier, dry_run=False)
        results = {item["result"] for item in delivered["deliveries"]}
        assert results == {"retired", "sent"}
        assert notification_statuses(config).count(("discord", "retired")) == 1
        assert notification_statuses(config).count(("discord", "sent")) == 1
        assert_exact_terminal_messages(notifier, concise_blocked(pr, current(config, pr)["blocker"]))
        second = FakeNotifier()
        again = userland.deliver_notifications(config, second, dry_run=False)
        assert again["deliveries"] == []
        assert second.sent == []


def test_malformed_persisted_rail_token_notifies_blocked_once() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        config = config_fixture(Path(temporary))
        pr = 141
        ingress(config, "pull_request", "spark-rail-pr", pr_payload(pr))
        set_head(config, pr, state="waiting_human", rail="spark", blocker="stale spark rail")
        trusted = userland.orchestration.resolve_trusted_enrollment(config)
        decision = userland.orchestration.decide_orchestration_outcome(
            userland.orchestration.outcome_from_review_row(
                current(config, pr),
                enrollment=trusted,
            )
        )
        assert decision["route"] == "fail_closed"
        assert decision["review_dispatch"] is False
        assert decision["notification"]["eligibility"] == "blocked"
        notifier = FakeNotifier()
        delivered = userland.deliver_notifications(config, notifier, dry_run=False)
        assert len(delivered["deliveries"]) == 3
        assert {item["result"] for item in delivered["deliveries"]} == {"sent"}
        assert_exact_terminal_messages(notifier, concise_blocked(pr, "unknown rail result"))
        second = FakeNotifier()
        again = userland.deliver_notifications(config, second, dry_run=False)
        assert again["deliveries"] == []
        assert second.sent == []


def test_complete_event_identity_prevents_retired_keys_from_suppressing_current() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        config = config_fixture(Path(temporary))
        pr = 142
        ingress(config, "pull_request", "complete-identity-pr", pr_payload(pr))
        ingress(
            config,
            "workflow_run",
            "complete-identity-ci",
            ci_payload(pr, 2142, conclusion="failure"),
        )
        row = current(config, pr)
        trusted = userland.orchestration.resolve_trusted_enrollment(config)
        decision = userland.orchestration.decide_orchestration_outcome(
            userland.orchestration.outcome_from_review_row(row, enrollment=trusted)
        )
        retired_identity = (
            f"{row['repository']}|{row['pr_number']}|{row['base_sha']}|"
            f"{row['head_sha']}|{row['review_epoch']}|{row['state']}|{row['repair_cycle']}|"
            f"{decision['route']}|{decision['reason']}|{decision['notification']['eligibility']}"
        )
        retired_key = hashlib.sha256(retired_identity.encode()).hexdigest()
        key_connection = core.open_database(Path(config["paths"]["state_root"]))
        try:
            current_key = userland.current_notification_event_key(
                key_connection,
                row,
                decision,
            )
        finally:
            key_connection.close()
        assert retired_key != current_key
        payload = {
            "schema": userland.NOTIFICATION_SCHEMA,
            "event_key": retired_key,
            "repository": row["repository"],
            "pr_number": pr,
            "base_sha": row["base_sha"],
            "head_sha": row["head_sha"],
            "review_epoch": row["review_epoch"],
            "state": row["state"],
            "repair_cycle": row["repair_cycle"],
            "message": concise_blocked(pr, current(config, pr)["blocker"]),
            "merge_authorized": False,
            "orchestration_outcome": {
                "route": decision["route"],
                "reason": decision["reason"],
            },
        }
        connection = core.open_database(Path(config["paths"]["state_root"]))
        try:
            userland.ensure_userland_tables(connection)
            for channel in userland.orchestration.NOTIFY_CHANNELS:
                connection.execute(
                    """
                    INSERT INTO notification_deliveries(
                      event_key, channel, repository, pr_number, base_sha, head_sha,
                      review_epoch, state, payload_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        retired_key,
                        channel,
                        row["repository"],
                        pr,
                        row["base_sha"],
                        row["head_sha"],
                        row["review_epoch"],
                        row["state"],
                        core.canonical_json(payload),
                        core.utc_now(),
                        core.utc_now(),
                    ),
                )
            connection.commit()
        finally:
            connection.close()
        same_route = userland.notification_event_identity(row, decision)
        assert decision["schema"] in same_route
        assert (decision["notification"]["kind"] or "") in same_route
        assert ",".join(decision["notification"]["channels"]) in same_route
        notifier = FakeNotifier()
        delivered = userland.deliver_notifications(config, notifier, dry_run=False)
        results = [item["result"] for item in delivered["deliveries"]]
        assert results.count("retired") == 3
        assert results.count("sent") == 3
        retired_keys = {item[0] for item in _notification_rows(config) if item[1] == "retired"}
        sent_keys = {item[0] for item in _notification_rows(config) if item[1] == "sent"}
        assert retired_keys == {retired_key}
        assert sent_keys == {current_key}
        assert_exact_terminal_messages(notifier, concise_blocked(pr, current(config, pr)["blocker"]))
        second = FakeNotifier()
        again = userland.deliver_notifications(config, second, dry_run=False)
        assert again["deliveries"] == []
        assert second.sent == []


def test_retry_attempt_revalidation_sends_only_the_current_event_key() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        config = config_fixture(root)
        pr = 143
        ingress(config, "pull_request", "attempt-key-pr", pr_payload(pr))
        ingress(config, "workflow_run", "attempt-key-ci", ci_payload(pr, 2143))
        failing = root / "failing-smoky"
        failing.write_text("#!/bin/sh\nexit 2\n", encoding="utf-8")
        failing.chmod(0o755)
        config["spark"]["smoky_path"] = str(failing)
        first = runtime.drain_actions(config, None, dry_run=False)
        assert first["actions"][0]["result"] == "failed"
        assert action(config, pr, "openclaw.enqueue")["attempts"] == 1
        userland.deliver_notifications(config, UnavailableNotifier(), dry_run=False)
        first_keys = {item[0] for item in _notification_rows(config) if item[1] == "pending"}
        assert len(first_keys) == 1
        retried = userland.retry_failed_openclaw(config, pr, apply=True)
        assert retried["result"] == "retried"
        runtime.drain_actions(config, None, dry_run=False)
        assert action(config, pr, "openclaw.enqueue")["attempts"] == 2
        notifier = FakeNotifier()
        delivered = userland.deliver_notifications(config, notifier, dry_run=False)
        results = [item["result"] for item in delivered["deliveries"]]
        assert results.count("retired") == 3
        assert results.count("sent") == 3
        statuses = notification_statuses(config)
        assert statuses.count(("discord", "retired")) == 1
        assert statuses.count(("discord", "sent")) == 1
        retired_keys = {item[0] for item in _notification_rows(config) if item[1] == "retired"}
        sent_keys = {item[0] for item in _notification_rows(config) if item[1] == "sent"}
        assert retired_keys == first_keys
        assert sent_keys
        assert sent_keys.isdisjoint(first_keys)
        assert_exact_terminal_messages(notifier, concise_blocked(pr, current(config, pr)["blocker"]))
        second = FakeNotifier()
        again = userland.deliver_notifications(config, second, dry_run=False)
        assert again["deliveries"] == []
        assert second.sent == []


def test_invalid_tuple_persisted_rows_fail_closed_without_review_dispatch() -> None:
    cases = (
        (118, "ci_failed", "clawsweeper"),
        (119, "openclaw_failed", None),
    )
    for pr, state, rail in cases:
        with tempfile.TemporaryDirectory() as temporary:
            config = config_fixture(Path(temporary))
            ingress(config, "pull_request", f"invalid-tuple-{pr}", pr_payload(pr))
            set_head(config, pr, state=state, rail=rail, blocker="stale invalid tuple")
            trusted = userland.orchestration.resolve_trusted_enrollment(config)
            decision = userland.orchestration.decide_orchestration_outcome(
                userland.orchestration.outcome_from_review_row(
                    current(config, pr),
                    enrollment=trusted,
                )
            )
            assert decision["route"] == "fail_closed"
            assert decision["review_dispatch"] is False
            assert decision["legacy_dispatch"] is False
            assert decision["reason"] == "unknown_rail_result_fail_closed"
            assert decision["notification"]["eligibility"] == "blocked"
            notifier = FakeNotifier()
            delivered = userland.deliver_notifications(config, notifier, dry_run=False)
            assert len(delivered["deliveries"]) == 3
            assert_exact_terminal_messages(notifier, concise_blocked(pr, "unknown rail result"))
            second = FakeNotifier()
            again = userland.deliver_notifications(config, second, dry_run=False)
            assert again["deliveries"] == []
            assert second.sent == []


def test_unenrolled_inconsistent_tuple_notifies_blocked_without_dispatch() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        config = config_fixture(Path(temporary))
        pr = 120
        ingress(config, "pull_request", "unenrolled-invalid-pr", pr_payload(pr))
        ingress(config, "workflow_run", "unenrolled-invalid-ci", ci_payload(pr, 2120))
        assert action(config, pr, "openclaw.enqueue")["status"] == "pending"
        set_trusted_enrollment(config, "absent", "absent")
        set_head(config, pr, state="ci_failed", rail="clawsweeper", blocker="stale invalid tuple")
        trusted = userland.orchestration.resolve_trusted_enrollment(config)
        decision = userland.orchestration.decide_orchestration_outcome(
            userland.orchestration.outcome_from_review_row(
                current(config, pr),
                enrollment=trusted,
            )
        )
        assert trusted == {"review_conductor": "absent", "legacy_xapi": "absent"}
        assert decision["route"] == "fail_closed"
        assert decision["review_dispatch"] is False
        assert decision["legacy_dispatch"] is False
        assert decision["notification"]["eligibility"] == "blocked"
        assert decision["reason"] == "unknown_rail_result_fail_closed"
        notifier = FakeNotifier()
        tick = userland.run_tick(config, None, notifier, dry_run=True)
        assert tick["worker"]["result"] == "skipped"
        assert tick["hydration"] == []
        assert tick["openclaw"] == []
        assert tick["clawsweeper"] == []
        outcomes = queued_notification_outcomes(config)
        assert outcomes
        for outcome in outcomes:
            assert outcome["route"] == "fail_closed"
            assert outcome["notification"]["eligibility"] == "blocked"
            assert outcome["reason"] == "unknown_rail_result_fail_closed"
        sent = userland.deliver_notifications(config, notifier, dry_run=False)
        assert len(sent["deliveries"]) == 3
        assert_exact_terminal_messages(notifier, concise_blocked(pr, "unknown rail result"))
        assert action(config, pr, "openclaw.enqueue")["status"] == "pending"


def _notification_rows(config: dict[str, Any]) -> list[tuple[str, str, str]]:
    connection = core.open_database(Path(config["paths"]["state_root"]))
    try:
        return [
            (row["event_key"], row["status"], row["payload_json"])
            for row in connection.execute(
                "SELECT event_key, status, payload_json FROM notification_deliveries "
                "ORDER BY channel, event_key"
            )
        ]
    finally:
        connection.close()


def test_run_tick_uses_trusted_enrollment_not_caller_payloads() -> None:
    stale_blocker = "stale persisted CI text must not win"
    cases = (
        (
            "present",
            "absent",
            "review_conductor",
            "human_action_required",
            True,
            stale_blocker,
        ),
        (
            "present",
            "present",
            "review_conductor",
            "human_action_required",
            True,
            stale_blocker,
        ),
        (
            "absent",
            "present",
            "legacy_xapi",
            "legacy_xapi_handoff_required",
            False,
            None,
        ),
        (
            "absent",
            "absent",
            "none",
            "unenrolled_no_review_no_notification",
            False,
            None,
        ),
        (
            "broken",
            "absent",
            "fail_closed",
            "ambiguous_or_broken_enrollment",
            True,
            "ambiguous or broken enrollment",
        ),
    )
    for index, (
        review_status,
        legacy_status,
        route,
        reason,
        notifies,
        expected_text,
    ) in enumerate(cases):
        with tempfile.TemporaryDirectory() as temporary:
            config = config_fixture(Path(temporary))
            pr = 90 + index
            ingress(config, "pull_request", f"enroll-{pr}", pr_payload(pr))
            set_head(
                config,
                pr,
                state="ci_failed",
                blocker=stale_blocker,
            )
            set_trusted_enrollment(config, review_status, legacy_status)
            caller_payload = {"review_conductor": "present", "legacy_xapi": "absent"}
            trusted = userland.orchestration.resolve_trusted_enrollment(config)
            assert trusted == {
                "review_conductor": review_status,
                "legacy_xapi": legacy_status,
            }
            assert userland.orchestration.effective_trusted_enrollment(
                config, caller_payload
            ) == (
                trusted
                if caller_payload == trusted
                else {"review_conductor": "broken", "legacy_xapi": "broken"}
            )
            notifier = FakeNotifier()
            tick = userland.run_tick(config, None, notifier, dry_run=True)
            outcomes = queued_notification_outcomes(config)
            if notifies:
                assert outcomes
                for outcome in outcomes:
                    assert outcome["route"] == route
                    assert outcome["reason"] == reason
                    assert outcome["notification"]["eligibility"] == "blocked"
                assert tick["notifications"]["deliveries"]
                sent = userland.deliver_notifications(config, notifier, dry_run=False)
                assert len(sent["deliveries"]) == 3
                assert_exact_terminal_messages(
                    notifier, concise_blocked(pr, expected_text)
                )
            else:
                assert outcomes == []
                assert tick["notifications"]["deliveries"] == []
                assert notifier.sent == []


def test_fail_closed_canonical_reason_beats_stale_blocker_text() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        config = config_fixture(Path(temporary))
        pr = 88
        ingress(config, "pull_request", "stale-blocker-pr", pr_payload(pr))
        set_head(
            config,
            pr,
            state="mystery_state",
            blocker="stale persisted CI text must not win",
        )
        notifier = FakeNotifier()
        delivered = userland.deliver_notifications(config, notifier, dry_run=False)
        outcomes = queued_notification_outcomes(config)
        assert outcomes
        for outcome in outcomes:
            assert outcome["route"] == "fail_closed"
            assert outcome["reason"] == "unknown_state_fail_closed"
        assert len(delivered["deliveries"]) == 3
        assert_exact_terminal_messages(notifier, concise_blocked(pr, "unknown state"))
        for _channel, message in notifier.sent:
            assert "stale persisted CI text must not win" not in message
            for fragment in VERBOSE_TERMINAL_FRAGMENTS:
                assert fragment not in message


def test_keep_open_without_defects_is_review_success_not_merge() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        config = config_fixture(root)
        pr = 91
        run_id = 2091
        prepare_openclaw(config, root, pr)
        prepare_clawsweeper_run(config, pr, run_id)
        client = FakeGitHub(
            run_id,
            claw_bundle(
                run_id=run_id,
                pr=pr,
                proof_status="not_applicable",
                decision="keep_open",
                process_gates=["own_current_check", "owner_merge_authority"],
            ),
        )
        collected = userland.collect_clawsweeper_terminals(
            config, client, dry_run=False
        )
        assert collected[0]["verdict"] == "clean"
        assert collected[0]["ready_qualified"] is False
        runtime.drain_bridge_inboxes(config)
        row = current(config, pr)
        assert row["state"] == "ready_for_human_merge"
        assert core.state_projection(dict(row))["checks"]["ClawSweeper Review Rail"] == "success"
        assert core.state_projection(dict(row))["merge_authorized"] is False


def test_sub_platinum_or_insufficient_proof_stops_at_human_gate() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        config = config_fixture(root)
        pr = 74
        run_id = 2074
        prepare_openclaw(config, root, pr)
        prepare_clawsweeper_run(config, pr, run_id)
        client = FakeGitHub(
            run_id,
            claw_bundle(
                run_id=run_id,
                pr=pr,
                overall="D",
                proof="D",
                proof_status="insufficient",
            ),
        )
        collected = userland.collect_clawsweeper_terminals(
            config, client, dry_run=False
        )
        assert collected[0]["verdict"] == "human_gate"
        assert collected[0]["ready_qualified"] is False
        runtime.drain_bridge_inboxes(config)
        assert current(config, pr)["state"] == "waiting_human"
        notifier = FakeNotifier()
        userland.deliver_notifications(config, notifier, dry_run=False)
        row = current(config, pr)
        assert_exact_terminal_messages(notifier, concise_blocked(pr, row["blocker"]))


def test_completed_clawsweeper_human_gate_owner_adjudication_projects_ready() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        config = config_fixture(root)
        pr = 75
        run_id = 2075
        prepare_openclaw(config, root, pr)
        prepare_clawsweeper_run(config, pr, run_id)
        collected = userland.collect_clawsweeper_terminals(
            config,
            FakeGitHub(
                run_id,
                claw_bundle(
                    run_id=run_id,
                    pr=pr,
                    overall="D",
                    proof="D",
                    proof_status="insufficient",
                ),
            ),
            dry_run=False,
        )
        assert collected[0]["verdict"] == "human_gate"
        runtime.drain_bridge_inboxes(config)
        waiting = current(config, pr)
        assert waiting["state"] == "waiting_human"
        assert waiting["rail"] == "clawsweeper"
        assert waiting["review_request_id"] == str(run_id)
        assert waiting["review_epoch"] == 0
        claw_action = action(config, pr, "clawsweeper.dispatch")
        outcome = core.ingest_internal_event(
            config_path=Path(config["core_config"]),
            state_root=Path(config["paths"]["state_root"]),
            event_payload={
                "schema": core.INTERNAL_EVENT_SCHEMA,
                "event_id": "userland-claw-proof-gap",
                "type": "adjudication.completed",
                "repository": "dinkuskit/blocks",
                "pr_number": pr,
                "base_sha": BASE,
                "head_sha": HEAD,
                "request_id": str(run_id),
                "rail": "clawsweeper",
                "classifications": ["defer", "reject_false_positive"],
                "reviewer_actor": "clawsweeper",
                "proof_ref": "proof/adjudication/userland-claw-proof-gap/ADJUDICATION.md",
                "review_epoch": 0,
            },
        )
        assert outcome["state"] == "ready_for_human_merge"
        assert outcome["action_created"] is False
        assert outcome["merge_dispatched"] is False
        ready = current(config, pr)
        assert ready["state"] == "ready_for_human_merge"
        assert ready["rail"] == "clawsweeper"
        assert ready["review_request_id"] == str(run_id)
        assert ready["review_epoch"] == 0
        assert ready["reviewer_actor"] == "clawsweeper"
        assert action(config, pr, "clawsweeper.dispatch")["action_id"] == claw_action["action_id"]
        projected = core.state_projection(dict(ready))
        assert projected["checks"]["ClawSweeper Review Rail"] == "success"
        assert projected["merge_authorized"] is False
        connection = core.open_database(Path(config["paths"]["state_root"]), "dinkuskit/blocks")
        try:
            quality = runtime.accepted_quality_row(connection, ready)
            assert quality["content_verdict"] == "proof_deficient"
            assert quality["workflow_run_id"] == str(run_id)
            effective = runtime.effective_quality(connection, ready, quality)
            assert quality["content_verdict"] == "proof_deficient"
            assert quality["workflow_run_id"] == str(run_id)
            assert effective["content_verdict"] == "clean"
            assert effective["workflow_run_id"] == str(run_id)
            assert "Original review content: proof_deficient" in effective["adjudication_reason"]
            assert "userland-claw-proof-gap" in effective["adjudication_reason"]
            report = runtime.projection_check_report(
                ready,
                effective,
                check_name="ClawSweeper Review Rail",
                check_state="success",
            )
        finally:
            connection.close()
        assert report["content_verdict"] == "clean"
        assert report["workflow_run_id"] == str(run_id)
        assert "Original review content: proof_deficient" in report["reason"]
        import review_result_projection as projection

        rendered = projection.check_output(
            "ClawSweeper Review Rail",
            "success",
            repository=ready["repository"],
            pr_number=int(ready["pr_number"]),
            head_sha=ready["head_sha"],
            stage=report.get("stage"),
            content_verdict=report.get("content_verdict"),
            process_gates=report.get("process_gates"),
            reason=report.get("reason"),
            workflow_run_id=report.get("workflow_run_id"),
            artifact_digest=report.get("artifact_digest"),
            report_url=report.get("report_url"),
        )
        assert "Review content: clean." in rendered["text"]
        assert "Original review content: proof_deficient" in rendered["text"]
        assert "Merge authorized: no" in rendered["text"]
        assert current(config, pr)["state"] == "ready_for_human_merge"


def test_unbound_clawsweeper_workflow_failure_alerts_without_guessing_a_pr() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        config = config_fixture(root)
        pr = 76
        run_id = 2076
        prepare_openclaw(config, root, pr)
        claw_action = action(config, pr, "clawsweeper.dispatch")
        mark_dispatched(config, claw_action["action_id"])
        ingress(
            config,
            "workflow_run",
            f"claw-failure-{run_id}",
            claw_workflow_payload(run_id, conclusion="failure"),
        )
        collected = userland.collect_clawsweeper_terminals(
            config,
            EmptyGitHub(),
            dry_run=False,
        )
        assert collected == [
            {
                "workflow_run_id": run_id,
                "result": "rail_failure_alerted",
                "conclusion": "failure",
            }
        ]
        # A repository failure is not evidence about this PR or a later PR.
        # Reconciliation must leave each unbound request queued, even after
        # dispatch. The collector's repository-level alert remains available.
        from test_openclaw_report_publication import RecordingTransportClient
        client = RecordingTransportClient(config)
        for candidate in (pr, pr + 1):
            if candidate != pr:
                prepare_openclaw(config, root, candidate)
            for dispatched in (False, True):
                candidate_action = action(config, candidate, "clawsweeper.dispatch")
                connection = core.open_database(Path(config["paths"]["state_root"]), "dinkuskit/blocks")
                try:
                    connection.execute(
                        "UPDATE actions SET status = ? WHERE action_id = ?",
                        ("dispatched" if dispatched else "pending", candidate_action["action_id"]),
                    )
                    connection.commit()
                finally:
                    connection.close()
                before = dict(current(config, candidate))
                expected = core.state_projection(before)
                projected = runtime.reconcile_projection(
                    config, None, pr_number=candidate, dry_run=True,
                )["projected"][0]
                assert projected["visible_state"] == expected["visible_state"]
                assert projected["checks"]["ClawSweeper Review Rail"] == "queued"
                runtime.reconcile_projection(config, client, pr_number=candidate)
                check = next(
                    check for check in client.checks.values()
                    if check["name"] == "ClawSweeper Review Rail"
                    and check["external_id"] == runtime.projection_external_id(
                        before, "ClawSweeper Review Rail",
                    )
                )
                assert check["status"] == "queued" and check.get("conclusion") is None
                assert str(run_id) not in check["output"]["summary"]
                assert "collection_attention_required" not in check["output"]["summary"]
                assert "Accepted artifact:" not in check["output"]["summary"]
                assert "Review content:" not in check["output"]["summary"]
                assert dict(current(config, candidate)) == before
        assert len(client.checks) == 4
        connection = core.open_database(Path(config["paths"]["state_root"]), "dinkuskit/blocks")
        try:
            failed = connection.execute(
                "SELECT * FROM rail_workflow_runs WHERE workflow_run_id = ?", (str(run_id),),
            ).fetchone()
            assert failed["status"] == "terminal_attention_required"
            assert failed["bound_repository"] is None and failed["bound_pr_number"] is None
            assert failed["verdict"] is None and failed["proof_ref"] is None
        finally:
            connection.close()
        notifier = FakeNotifier()
        userland.deliver_notifications(config, notifier, dry_run=False)
        assert [channel for channel, _message in notifier.sent] == [
            "openclaw_context",
            "discord",
            "signal",
        ]
        assert all("No PR was marked ready" in message for _channel, message in notifier.sent)
        assert all("No merge was attempted" in message for _channel, message in notifier.sent)



def test_bound_failed_workflow_without_bundle_is_execution_failure() -> None:
    for binding in ("current", "wrong_run", "stale_epoch"):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = config_fixture(root)
            pr, run_id = 78, 2078
            prepare_openclaw(config, root, pr)
            claw_action = action(config, pr, "clawsweeper.dispatch")
            mark_dispatched(config, claw_action["action_id"])
            connection = core.open_database(Path(config["paths"]["state_root"]), "dinkuskit/blocks")
            try:
                core.process_internal_event(connection, core.load_config(Path(config["core_config"])), {
                    "schema": core.INTERNAL_EVENT_SCHEMA, "event_id": "fixture-claw-start",
                    "type": "clawsweeper.started", "repository": "dinkuskit/blocks",
                    "pr_number": pr, "base_sha": BASE, "head_sha": HEAD,
                    "review_epoch": claw_action["review_epoch"],
                    "workflow_run_id": str(run_id if binding != "wrong_run" else run_id + 1),
                })
                if binding == "stale_epoch":
                    connection.execute("UPDATE heads SET review_epoch = review_epoch + 1")
                connection.commit()
            finally:
                connection.close()
            ingress(config, "workflow_run", "fixture-claw-failed", claw_workflow_payload(run_id, conclusion="failure"))
            outcomes = userland.collect_clawsweeper_terminals(config, EmptyGitHub(), dry_run=False)
            row = current(config, pr)
            if binding == "current":
                assert outcomes[0]["result"] == "bound_execution_failed"
                assert row["state"] == "clawsweeper_failed"
                assert "no qualified verdict bundle" in row["blocker"]
                report = runtime.projection_check_report(row, check_name="ClawSweeper Review Rail", check_state="failure")
                payload = runtime.check_payload("ClawSweeper Review Rail", HEAD, "fixture-check", "failure", report=report)
                assert payload["status"] == "completed" and payload["conclusion"] == "failure"
                assert "Review content:" not in payload["output"]["summary"]
                assert report["workflow_run_id"] == str(run_id)
            else:
                assert outcomes[0]["result"] == "rail_failure_alerted"
                assert row["state"] == "clawsweeper_running"
            connection = core.open_database(Path(config["paths"]["state_root"]), "dinkuskit/blocks")
            try:
                receipt = connection.execute("SELECT * FROM rail_workflow_runs WHERE workflow_run_id = ?", (str(run_id),)).fetchone()
                assert receipt["verdict"] is None and receipt["proof_ref"] is None
                assert (receipt["bound_pr_number"] == pr) == (binding == "current")
            finally:
                connection.close()
            assert userland.collect_clawsweeper_terminals(config, EmptyGitHub(), dry_run=False) == []
            assert list(Path(config["clawsweeper_bridge"]["terminal_inbox"]).glob("*.json")) == []


class HistoricalThenCurrentGitHub:
    def __init__(self, old_run: int, current_run: int, old_bundle: bytes, current_bundle: bytes | None, identity: dict[str, Any] | None) -> None:
        self.old_run = old_run
        self.current_run = current_run
        self.old_bundle = old_bundle
        self.current_bundle = current_bundle
        self.identity = identity

    def list_run_artifacts(self, run_id: int) -> list[dict[str, Any]]:
        if run_id == self.old_run or (run_id == self.current_run and self.current_bundle is not None):
            return [{"id": run_id, "name": f"dinkuskit-native-review-{run_id}-1", "expired": False}]
        return []

    def download_artifact(self, artifact_id: int) -> bytes:
        if artifact_id == self.old_run:
            return self.old_bundle
        if artifact_id == self.current_run and self.current_bundle is not None:
            return self.current_bundle
        raise AssertionError(artifact_id)

    def clawsweeper_dispatch_identity(self, run_id: int) -> dict[str, Any] | None:
        if run_id == self.current_run:
            return self.identity
        return None


def test_historical_bundle_does_not_block_current_no_artifact_failure() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        config = config_fixture(root)
        pr = 48
        old_run, failed_run = 35560312902, 36009939846
        stale_head = "9" * 40
        prepare_openclaw(config, root, pr)
        claw_action = action(config, pr, "clawsweeper.dispatch")
        mark_dispatched(config, claw_action["action_id"])
        old_payload = claw_workflow_payload(old_run, conclusion="success")
        old_payload["workflow_run"]["created_at"] = "2026-09-21T04:15:57Z"
        failed_payload = claw_workflow_payload(failed_run, conclusion="failure")
        failed_payload["workflow_run"]["created_at"] = "2026-09-24T14:02:54Z"
        ingress(config, "workflow_run", "historical-success", old_payload)
        ingress(config, "workflow_run", "current-failure", failed_payload)
        identity = {
            "repository": "dinkuskit/blocks",
            "pr_number": pr,
            "base_sha": BASE,
            "head_sha": HEAD,
            "review_epoch": 0,
        }
        client = HistoricalThenCurrentGitHub(
            old_run,
            failed_run,
            claw_bundle(run_id=old_run, pr=pr, head_sha=stale_head),
            None,
            identity,
        )
        collected = userland.collect_clawsweeper_terminals(config, client, dry_run=False)
        assert collected == [
            {
                "workflow_run_id": old_run,
                "result": "historical_bundle_retired",
                "conclusion": "success",
            },
            {
                "workflow_run_id": failed_run,
                "result": "bound_execution_failed",
                "conclusion": "failure",
                "binding": {**identity},
            },
        ]
        row = current(config, pr)
        assert row["state"] == "clawsweeper_failed"
        assert row["review_request_id"] == str(failed_run)
        assert "no qualified verdict bundle" in row["blocker"]
        assert core.state_projection(dict(row))["checks"]["ClawSweeper Review Rail"] == "failure"
        assert core.state_projection(dict(row))["merge_authorized"] is False
        projected = runtime.reconcile_projection(config, None, pr_number=pr, dry_run=True)
        assert projected["projected"][0]["visible_state"] == "clawsweeper_failed"
        assert projected["projected"][0]["checks"]["ClawSweeper Review Rail"] == "failure"
        assert projected["projected"][0]["merge_authorized"] is False
        report = runtime.projection_check_report(
            row, check_name="ClawSweeper Review Rail", check_state="failure"
        )
        payload = runtime.check_payload(
            "ClawSweeper Review Rail", HEAD, "fixture-check", "failure", report=report
        )
        assert payload["conclusion"] == "failure"
        assert "Review content:" not in payload["output"]["summary"]
        connection = core.open_database(Path(config["paths"]["state_root"]), "dinkuskit/blocks")
        try:
            historical = connection.execute(
                "SELECT * FROM rail_workflow_runs WHERE workflow_run_id = ?", (str(old_run),)
            ).fetchone()
            failed = connection.execute(
                "SELECT * FROM rail_workflow_runs WHERE workflow_run_id = ?", (str(failed_run),)
            ).fetchone()
            assert historical["status"] == "terminal_attention_required"
            assert historical["verdict"] is None and historical["bound_pr_number"] is None
            assert failed["status"] == "terminal_attention_required"
            assert failed["verdict"] is None and failed["proof_ref"] is None
            assert failed["bound_pr_number"] == pr and failed["bound_head_sha"] == HEAD
            assert connection.execute("SELECT COUNT(*) FROM clawsweeper_quality").fetchone()[0] == 0
        finally:
            connection.close()
        assert list(Path(config["clawsweeper_bridge"]["terminal_inbox"]).glob("*.json")) == []
        assert userland.collect_clawsweeper_terminals(config, client, dry_run=False) == []


def test_historical_success_bundle_still_allows_the_current_clean_terminal() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        config = config_fixture(root)
        pr = 49
        old_run, current_run = 11, 22
        prepare_openclaw(config, root, pr)
        mark_dispatched(config, action(config, pr, "clawsweeper.dispatch")["action_id"])
        old_payload = claw_workflow_payload(old_run)
        old_payload["workflow_run"]["created_at"] = "2026-09-21T04:15:57Z"
        current_payload = claw_workflow_payload(current_run)
        current_payload["workflow_run"]["created_at"] = "2026-09-24T14:02:54Z"
        ingress(config, "workflow_run", "old-clean", old_payload)
        ingress(config, "workflow_run", "current-clean", current_payload)
        client = HistoricalThenCurrentGitHub(
            old_run,
            current_run,
            claw_bundle(run_id=old_run, pr=pr, head_sha="8" * 40),
            claw_bundle(run_id=current_run, pr=pr),
            None,
        )
        collected = userland.collect_clawsweeper_terminals(config, client, dry_run=False)
        assert collected[0]["result"] == "historical_bundle_retired"
        assert collected[1]["result"] == "terminal_materialized"
        assert collected[1]["verdict"] == "clean"
        runtime.drain_bridge_inboxes(config)
        assert current(config, pr)["state"] == "ready_for_human_merge"
        connection = core.open_database(Path(config["paths"]["state_root"]), "dinkuskit/blocks")
        try:
            assert connection.execute(
                "SELECT COUNT(*) FROM clawsweeper_quality WHERE head_sha = ?", ("8" * 40,)
            ).fetchone()[0] == 0
        finally:
            connection.close()


def _set_action_status(
    config: dict[str, Any], action_id: str, status: str, receipt: dict[str, Any]
) -> None:
    connection = core.open_database(Path(config["paths"]["state_root"]), "dinkuskit/blocks")
    try:
        connection.execute(
            "UPDATE actions SET status = ?, receipt_json = ? WHERE action_id = ?",
            (status, core.canonical_json(receipt), action_id),
        )
        connection.commit()
    finally:
        connection.close()


def test_inflight_dispatch_bundle_stays_pending() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        config = config_fixture(root)
        pr, run_id = 90, 4090
        prepare_openclaw(config, root, pr)
        claw_action = action(config, pr, "clawsweeper.dispatch")
        _set_action_status(config, claw_action["action_id"], "dispatching", {})
        ingress(config, "workflow_run", "inflight-success", claw_workflow_payload(run_id))
        client = HistoricalThenCurrentGitHub(
            1,
            run_id,
            b"",
            claw_bundle(run_id=run_id, pr=pr),
            None,
        )
        collected = userland.collect_clawsweeper_terminals(config, client, dry_run=False)
        assert collected == [
            {
                "workflow_run_id": run_id,
                "result": "current_dispatch_pending",
                "conclusion": "success",
            }
        ]
        assert current(config, pr)["state"] == "clawsweeper_queued"
        connection = core.open_database(Path(config["paths"]["state_root"]), "dinkuskit/blocks")
        try:
            receipt = connection.execute(
                "SELECT * FROM rail_workflow_runs WHERE workflow_run_id = ?",
                (str(run_id),),
            ).fetchone()
            assert receipt["status"] == "terminal_pending_verdict_bridge"
            assert receipt["verdict"] is None and receipt["proof_ref"] is None
        finally:
            connection.close()
        assert list(Path(config["clawsweeper_bridge"]["terminal_inbox"]).glob("*.json")) == []
        _set_action_status(
            config,
            claw_action["action_id"],
            "dispatched",
            {"result": "dispatched", "workflow_run_id": run_id},
        )
        again = userland.collect_clawsweeper_terminals(config, client, dry_run=False)
        assert again[0]["result"] == "terminal_materialized"
        assert again[0]["verdict"] == "clean"


def test_reopened_epoch_retires_previous_run_and_keeps_current_bundle() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        config = config_fixture(root)
        pr = 91
        old_run, current_run = 11, 22
        prepare_openclaw(config, root, pr)
        previous = action(config, pr, "clawsweeper.dispatch")
        _set_action_status(
            config,
            previous["action_id"],
            "dispatched",
            {"result": "dispatched", "workflow_run_id": old_run},
        )
        connection = core.open_database(Path(config["paths"]["state_root"]), "dinkuskit/blocks")
        try:
            connection.execute(
                "UPDATE heads SET review_epoch = 1 WHERE pr_number = ? AND is_current = 1",
                (pr,),
            )
            payload = json.loads(previous["payload_json"])
            payload["review_epoch"] = 1
            core.insert_action(
                connection,
                kind="clawsweeper.dispatch",
                repository="dinkuskit/blocks",
                pr_number=pr,
                base_sha=BASE,
                head_sha=HEAD,
                payload=payload,
                suffix="reopen",
                review_epoch=1,
            )
            connection.commit()
            inserted = connection.execute(
                """
                SELECT action_id FROM actions
                WHERE pr_number = ? AND kind = 'clawsweeper.dispatch' AND review_epoch = 1
                """,
                (pr,),
            ).fetchone()
            assert inserted is not None
            current_action_id = inserted["action_id"]
        finally:
            connection.close()
        _set_action_status(
            config,
            current_action_id,
            "dispatched",
            {"result": "dispatched", "workflow_run_id": current_run},
        )
        old_payload = claw_workflow_payload(old_run)
        old_payload["workflow_run"]["created_at"] = "2026-09-21T04:15:57Z"
        current_payload = claw_workflow_payload(current_run)
        current_payload["workflow_run"]["created_at"] = "2026-09-24T14:02:54Z"
        ingress(config, "workflow_run", "epoch-old", old_payload)
        ingress(config, "workflow_run", "epoch-current", current_payload)
        client = HistoricalThenCurrentGitHub(
            old_run,
            current_run,
            claw_bundle(run_id=old_run, pr=pr, review_epoch=0),
            claw_bundle(run_id=current_run, pr=pr, review_epoch=1),
            None,
        )
        collected = userland.collect_clawsweeper_terminals(config, client, dry_run=False)
        assert collected[0]["result"] == "historical_bundle_retired"
        assert collected[1]["result"] == "terminal_materialized"
        assert collected[1]["verdict"] == "clean"
        runtime.drain_bridge_inboxes(config)
        row = current(config, pr)
        assert row["review_epoch"] == 1
        assert row["state"] == "ready_for_human_merge"
        connection = core.open_database(Path(config["paths"]["state_root"]), "dinkuskit/blocks")
        try:
            assert connection.execute(
                "SELECT COUNT(*) FROM clawsweeper_quality WHERE review_epoch = 0"
            ).fetchone()[0] == 0
            assert connection.execute(
                "SELECT COUNT(*) FROM clawsweeper_quality WHERE review_epoch = 1"
            ).fetchone()[0] == 1
        finally:
            connection.close()


def test_ambiguous_current_dispatches_stay_pending() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        config = config_fixture(root)
        pr, run_id = 92, 4092
        prepare_openclaw(config, root, pr)
        first = action(config, pr, "clawsweeper.dispatch")
        mark_dispatched(config, first["action_id"])
        connection = core.open_database(Path(config["paths"]["state_root"]), "dinkuskit/blocks")
        try:
            payload = json.loads(first["payload_json"])
            core.insert_action(
                connection,
                kind="clawsweeper.dispatch",
                repository="dinkuskit/blocks",
                pr_number=pr,
                base_sha=BASE,
                head_sha=HEAD,
                payload=payload,
                suffix="second",
                review_epoch=0,
            )
            connection.commit()
            second = connection.execute(
                """
                SELECT action_id FROM actions
                WHERE pr_number = ? AND kind = 'clawsweeper.dispatch' AND action_id != ?
                """,
                (pr, first["action_id"]),
            ).fetchone()
            assert second is not None
            second_id = second["action_id"]
        finally:
            connection.close()
        mark_dispatched(config, second_id)
        ingress(config, "workflow_run", "ambiguous", claw_workflow_payload(run_id))
        client = HistoricalThenCurrentGitHub(
            1, run_id, b"", claw_bundle(run_id=run_id, pr=pr), None
        )
        collected = userland.collect_clawsweeper_terminals(config, client, dry_run=False)
        assert collected == [
            {
                "workflow_run_id": run_id,
                "result": "current_dispatch_pending",
                "conclusion": "success",
            }
        ]
        assert current(config, pr)["state"] == "clawsweeper_queued"
        connection = core.open_database(Path(config["paths"]["state_root"]), "dinkuskit/blocks")
        try:
            receipt = connection.execute(
                "SELECT status, verdict FROM rail_workflow_runs WHERE workflow_run_id = ?",
                (str(run_id),),
            ).fetchone()
            assert receipt["status"] == "terminal_pending_verdict_bridge"
            assert receipt["verdict"] is None
        finally:
            connection.close()


class BundleRetirementMutationTarget(unittest.TestCase):
    def test_inflight_dispatch_bundle_stays_pending(self) -> None:
        test_inflight_dispatch_bundle_stays_pending()

    def test_reopened_epoch_retires_previous_run_and_keeps_current_bundle(self) -> None:
        test_reopened_epoch_retires_previous_run_and_keeps_current_bundle()


class ReceiptAndJobPageMutationTarget(unittest.TestCase):
    def test_malformed_receipt_does_not_own_a_current_bundle(self) -> None:
        test_malformed_receipt_does_not_own_a_current_bundle()

    def test_inflight_no_artifact_failure_stays_pending(self) -> None:
        test_inflight_no_artifact_failure_stays_pending()

    def test_admission_job_on_a_later_page_is_the_dispatch_identity(self) -> None:
        test_admission_job_on_a_later_page_is_the_dispatch_identity()

    def test_admission_job_lookup_does_not_read_past_the_page_cap(self) -> None:
        test_admission_job_lookup_does_not_read_past_the_page_cap()

    def test_string_receipt_run_id_is_not_a_stored_identity(self) -> None:
        test_string_receipt_run_id_is_not_a_stored_identity()

    def test_string_receipt_run_id_does_not_pass_the_admission_fence(self) -> None:
        test_string_receipt_run_id_does_not_pass_the_admission_fence()

    def test_malformed_admission_job_id_stays_pending_and_collection_continues(self) -> None:
        test_malformed_admission_job_id_stays_pending_and_collection_continues()

    def test_overlong_bundle_epoch_does_not_abort_later_collection(self) -> None:
        test_overlong_bundle_epoch_does_not_abort_later_collection()


def test_bundle_retirement_mutants_fail_their_intended_tests() -> None:
    mutants = (
        (
            "tools/review_conductor_userland.py",
            "         AND heads.review_epoch = actions.review_epoch -- current-head epoch\n",
            "",
            "test_review_conductor_userland.BundleRetirementMutationTarget."
            "test_reopened_epoch_retires_previous_run_and_keeps_current_bundle",
        ),
        (
            "tools/review_conductor_userland.py",
            'IN_FLIGHT_CLAWSWEEPER_STATUSES = ("pending", "preparing", "dispatching", "reconcile_required")\n',
            'IN_FLIGHT_CLAWSWEEPER_STATUSES = ("pending", "preparing", "reconcile_required")\n',
            "test_review_conductor_userland.BundleRetirementMutationTarget."
            "test_inflight_dispatch_bundle_stays_pending",
        ),
        (
            "tools/review_conductor_userland.py",
            "    if not isinstance(receipt, dict):  # malformed-receipt-owns-nothing\n"
            "        return False\n",
            "",
            "test_review_conductor_userland.ReceiptAndJobPageMutationTarget."
            "test_malformed_receipt_does_not_own_a_current_bundle",
        ),
        (
            "tools/review_conductor_userland.py",
            "                if bound_failure is None and _no_artifact_failure_awaits_current_dispatch(\n",
            "                if False and bound_failure is None and _no_artifact_failure_awaits_current_dispatch(\n",
            "test_review_conductor_userland.ReceiptAndJobPageMutationTarget."
            "test_inflight_no_artifact_failure_stays_pending",
        ),
        (
            "tools/review_conductor_runtime.py",
            "            current = nxt  # clawsweeper-job-pages\n",
            "            return jobs  # clawsweeper-job-pages\n",
            "test_review_conductor_userland.ReceiptAndJobPageMutationTarget."
            "test_admission_job_on_a_later_page_is_the_dispatch_identity",
        ),
        (
            "tools/review_conductor_runtime.py",
            "            if page_index == MAX_LIST_PAGES - 1:  # clawsweeper-job-page-cap\n",
            "            if page_index == MAX_LIST_PAGES + 99:  # clawsweeper-job-page-cap\n",
            "test_review_conductor_userland.ReceiptAndJobPageMutationTarget."
            "test_admission_job_lookup_does_not_read_past_the_page_cap",
        ),
        (
            "tools/review_conductor_userland.py",
            "        if (isinstance(recorded, bool) or not isinstance(recorded, int)\n"
            "                or recorded != int(run_id)):  # integer-receipt-hit\n",
            "        if recorded is None or str(recorded) != run_id:  # integer-receipt-hit\n",
            "test_review_conductor_userland.ReceiptAndJobPageMutationTarget."
            "test_string_receipt_run_id_is_not_a_stored_identity",
        ),
        (
            "tools/review_conductor_userland.py",
            "        if (isinstance(recorded, bool) or not isinstance(recorded, int)\n"
            "                or recorded != int(rail_run[\"workflow_run_id\"])):  # integer-receipt-fence\n",
            "        if recorded is not None and str(recorded) != str(rail_run[\"workflow_run_id\"]):  # integer-receipt-fence\n",
            "test_review_conductor_userland.ReceiptAndJobPageMutationTarget."
            "test_string_receipt_run_id_does_not_pass_the_admission_fence",
        ),
        (
            "tools/review_conductor_runtime.py",
            "            raise GitHubApiError(\"ClawSweeper admission job id is invalid\") from exc\n",
            "            return None\n",
            "test_review_conductor_userland.ReceiptAndJobPageMutationTarget."
            "test_malformed_admission_job_id_stays_pending_and_collection_continues",
        ),
        (
            "tools/review_conductor_userland.py",
            "        if re.fullmatch(r\"[0-9]{1,10}\", epoch) is None:  # bundle-epoch-digit-bound\n",
            "        if re.fullmatch(r\"[0-9]+\", epoch) is None:  # bundle-epoch-digit-bound\n",
            "test_review_conductor_userland.ReceiptAndJobPageMutationTarget."
            "test_overlong_bundle_epoch_does_not_abort_later_collection",
        ),
    )
    for relative, old, new, test_id in mutants:
        with tempfile.TemporaryDirectory() as temporary:
            copy_root = Path(temporary) / "copy"
            for name in ("tools", "tests", "contracts", "examples"):
                shutil.copytree(ROOT / name, copy_root / name, ignore=shutil.ignore_patterns("__pycache__"))
            target = copy_root / relative
            source = target.read_text()
            assert source.count(old) == 1, relative
            target.write_text(source.replace(old, new, 1))
            completed = subprocess.run(
                [sys.executable, "-m", "unittest", "-q", test_id],
                cwd=copy_root / "tests",
                capture_output=True,
                text=True,
                timeout=180,
                env={"PATH": "/usr/bin:/bin", "HOME": temporary, "PYTHONDONTWRITEBYTECODE": "1"},
            )
            assert completed.returncode != 0, completed.stderr
            assert "Ran 1 test" in completed.stderr, completed.stderr
            assert f"FAIL: {test_id.rsplit('.', 1)[1]}" in completed.stderr or (
                f"ERROR: {test_id.rsplit('.', 1)[1]}" in completed.stderr
            ), completed.stderr


def test_unproven_no_artifact_failure_does_not_choose_a_pull_request() -> None:
    cases = (
        {"review_epoch": 1, "head_sha": HEAD},
        {"review_epoch": 0, "head_sha": "7" * 40},
        {"review_epoch": True, "head_sha": HEAD},
    )
    for index, override in enumerate(cases):
        with tempfile.TemporaryDirectory() as temporary:
            config = config_fixture(Path(temporary))
            pr, run_id = 80 + index, 3000 + index
            prepare_openclaw(config, Path(temporary), pr)
            dispatched = action(config, pr, "clawsweeper.dispatch")
            mark_dispatched(config, dispatched["action_id"])
            if index == 2:
                connection = core.open_database(Path(config["paths"]["state_root"]), "dinkuskit/blocks")
                try:
                    payload = json.loads(dispatched["payload_json"])
                    payload["previous_workflow_run_id"] = str(run_id)
                    connection.execute(
                        "UPDATE actions SET payload_json = ? WHERE action_id = ?",
                        (core.canonical_json(payload), dispatched["action_id"]),
                    )
                    connection.commit()
                finally:
                    connection.close()
            ingress(
                config,
                "workflow_run",
                f"unproven-{run_id}",
                claw_workflow_payload(run_id, conclusion="failure"),
            )
            identity = {
                "repository": "dinkuskit/blocks",
                "pr_number": pr,
                "base_sha": BASE,
                "head_sha": override["head_sha"],
                "review_epoch": override["review_epoch"],
            }
            if index == 2:
                identity["review_epoch"] = 0
                identity["head_sha"] = HEAD
            client = HistoricalThenCurrentGitHub(1, run_id, b"", None, identity)
            outcomes = userland.collect_clawsweeper_terminals(config, client, dry_run=False)
            assert outcomes[0]["result"] == "rail_failure_alerted"
            assert current(config, pr)["state"] == "clawsweeper_queued"


def test_stored_dispatch_run_id_fails_queued_execution_without_a_log() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        config = config_fixture(Path(temporary))
        pr, run_id = 84, 3084
        prepare_openclaw(config, Path(temporary), pr)
        dispatched = action(config, pr, "clawsweeper.dispatch")
        connection = core.open_database(Path(config["paths"]["state_root"]), "dinkuskit/blocks")
        try:
            connection.execute(
                "UPDATE actions SET status = 'dispatched', receipt_json = ? WHERE action_id = ?",
                (
                    core.canonical_json({"result": "dispatched", "workflow_run_id": run_id}),
                    dispatched["action_id"],
                ),
            )
            connection.commit()
        finally:
            connection.close()
        ingress(config, "workflow_run", "stored-run", claw_workflow_payload(run_id, conclusion="failure"))
        outcomes = userland.collect_clawsweeper_terminals(config, EmptyGitHub(), dry_run=False)
        assert outcomes[0]["result"] == "bound_execution_failed"
        row = current(config, pr)
        assert row["state"] == "clawsweeper_failed"
        assert row["review_request_id"] == str(run_id)
        assert core.state_projection(dict(row))["checks"]["ClawSweeper Review Rail"] == "failure"


def test_dispatch_identity_transport_failure_leaves_the_run_pending() -> None:
    class UnavailableIdentity:
        def list_run_artifacts(self, _run_id: int) -> list[dict[str, Any]]:
            return []

        def clawsweeper_dispatch_identity(self, _run_id: int) -> dict[str, Any]:
            raise runtime.GitHubTransientError("fixture transport")

    with tempfile.TemporaryDirectory() as temporary:
        config = config_fixture(Path(temporary))
        pr, run_id = 85, 3085
        prepare_openclaw(config, Path(temporary), pr)
        mark_dispatched(config, action(config, pr, "clawsweeper.dispatch")["action_id"])
        ingress(config, "workflow_run", "retry-identity", claw_workflow_payload(run_id, conclusion="failure"))
        outcomes = userland.collect_clawsweeper_terminals(config, UnavailableIdentity(), dry_run=False)
        assert outcomes == [{
            "workflow_run_id": run_id,
            "result": "dispatch_identity_unavailable",
            "conclusion": "failure",
        }]
        assert current(config, pr)["state"] == "clawsweeper_queued"
        connection = core.open_database(Path(config["paths"]["state_root"]), "dinkuskit/blocks")
        try:
            receipt = connection.execute(
                "SELECT status FROM rail_workflow_runs WHERE workflow_run_id = ?", (str(run_id),)
            ).fetchone()
            assert receipt["status"] == "terminal_pending_verdict_bridge"
        finally:
            connection.close()


def test_parse_clawsweeper_dispatch_log_accepts_one_consistent_tuple() -> None:
    head = "c" * 40
    base = "d" * 40
    text = "\n".join([
        "2026-09-24T14:03:00.4561187Z   pr_number: 48",
        "2026-09-24T14:03:00.4562649Z   expected_base_sha: " + base,
        "2026-09-24T14:03:00.4564365Z   expected_head_sha: " + head,
        "2026-09-24T14:03:00.4565654Z   review_epoch: 0",
        "other line",
        "PR_NUMBER: 48",
        "EXPECTED_BASE_SHA: " + base,
        "EXPECTED_HEAD_SHA: " + head,
        "REVIEW_EPOCH: 0",
    ])
    assert runtime.parse_clawsweeper_dispatch_log(text) == {
        "pr_number": 48,
        "expected_base_sha": base,
        "expected_head_sha": head,
        "review_epoch": 0,
    }
    conflict = text + "\npr_number: 49\nexpected_base_sha: " + base + "\nexpected_head_sha: " + ("e" * 40) + "\nreview_epoch: 0\n"
    assert runtime.parse_clawsweeper_dispatch_log(conflict) is None
    assert runtime.parse_clawsweeper_dispatch_log("pr_number: 48\nreview_epoch: 0\n") is None
    assert runtime.parse_clawsweeper_dispatch_log("pr_number: 0\nexpected_base_sha: " + base + "\nexpected_head_sha: " + head + "\nreview_epoch: 0\n") is None
    echoed = text + "\npr_number: 48\nexpected_base_sha: " + base
    assert runtime.parse_clawsweeper_dispatch_log(echoed)["pr_number"] == 48
    partial_conflict = text + "\npr_number: 49\n"
    assert runtime.parse_clawsweeper_dispatch_log(partial_conflict) is None
    leading_conflict = "pr_number: 49\nnoise\n" + text
    assert runtime.parse_clawsweeper_dispatch_log(leading_conflict) is None


def _is_jobs_url(url: str) -> bool:
    return url.split("?", 1)[0].endswith("/jobs")


def _jobs_page_number(url: str) -> int:
    query = url.split("?", 1)[1] if "?" in url else ""
    for part in query.split("&"):
        if part.startswith("page="):
            return int(part.split("=", 1)[1])
    return 1


def test_admission_job_log_is_the_only_dispatch_identity_source() -> None:
    head = "a" * 40
    base = "b" * 40
    log = "\n".join([
        "pr_number: 48",
        "expected_base_sha: " + base,
        "expected_head_sha: " + head,
        "review_epoch: 2",
    ]).encode()
    calls: list[str] = []

    def transport(method: str, url: str, _headers: dict[str, str], body: bytes | None, _timeout: float):
        calls.append(url)
        if _is_jobs_url(url):
            assert url.endswith("/jobs?per_page=100")
            payload = {"jobs": [
                {"id": 7, "name": "Run pinned exact-tuple ClawSweeper / Admit exact-tuple PR"},
                {"id": 8, "name": "Run pinned exact-tuple ClawSweeper / Native ClawSweeper review"},
            ]}
            return 200, {}, json.dumps(payload).encode()
        if url.endswith("/logs"):
            return 200, {}, log
        if body and b"return_run_details" in body:
            return 200, {}, json.dumps({"workflow_run_id": 99}).encode()
        return 204, {}, b""

    with tempfile.TemporaryDirectory() as temporary:
        config = config_fixture(Path(temporary))
        client = runtime.GitHubAppClient(config, "fixture-key", transport=transport)
        client._token = "fixture-token"
        client._token_expires = 9999999999
        assert client.clawsweeper_dispatch_identity(36009939846) == {
            "repository": "dinkuskit/blocks",
            "pr_number": 48,
            "base_sha": base,
            "head_sha": head,
            "review_epoch": 2,
        }
        assert any(_is_jobs_url(url) and url.endswith("?per_page=100") for url in calls)
        assert any(url.endswith("/logs") for url in calls)
        run_id = client.dispatch_clawsweeper(
            pr_number=48, base_sha=base, head_sha=head, publish=True
        )
        assert run_id == 99
        calls.clear()

        def no_admission(method: str, url: str, _headers: dict[str, str], _body: bytes | None, _timeout: float):
            calls.append(url)
            assert _is_jobs_url(url) and url.endswith("?per_page=100")
            return 200, {}, json.dumps({"jobs": [{"id": 8, "name": "Native ClawSweeper review"}]}).encode()

        client._transport = no_admission
        assert client.clawsweeper_dispatch_identity(36009939846) is None
        assert len(calls) == 1 and _is_jobs_url(calls[0])


def _dispatch_tuple_text(pr: int, base: str, head: str, epoch: int) -> str:
    return "\n".join([
        f"pr_number: {pr}",
        f"expected_base_sha: {base}",
        f"expected_head_sha: {head}",
        f"review_epoch: {epoch}",
    ]) + "\n"


def _admission_log_client(
    config: dict[str, Any],
    request_once: runtime.ArtifactRequest,
) -> runtime.GitHubAppClient:
    def transport(
        method: str, url: str, _headers: dict[str, str], body: bytes | None, _timeout: float
    ):
        if _is_jobs_url(url):
            payload = {"jobs": [{"id": 7, "name": "Run pinned exact-tuple ClawSweeper / Admit exact-tuple PR"}]}
            return 200, {}, json.dumps(payload).encode()
        if url.endswith("/artifacts"):
            return 200, {}, b'{"artifacts":[]}'
        if body and b"return_run_details" in body:
            return 200, {}, json.dumps({"workflow_run_id": 99}).encode()
        raise AssertionError(url)

    def artifact_transport(url: str, headers: dict[str, str], timeout: float) -> tuple[int, bytes]:
        return runtime.urllib_artifact_transport(url, headers, timeout, request_once=request_once)

    client = runtime.GitHubAppClient(
        config, "fixture-key", transport=transport, artifact_transport=artifact_transport
    )
    client._token = "fixture-token"
    client._token_expires = 9999999999
    return client


def test_oversize_admission_log_with_conflicting_suffix_is_not_an_identity() -> None:
    other = "e" * 40
    pr, run_id = 86, 3086
    prefix = _dispatch_tuple_text(pr, BASE, HEAD, 0).encode()
    limit = runtime.CLAWSWEEPER_DISPATCH_LOG_LIMIT
    assert len(prefix) < limit
    padding = b"x" * (limit - len(prefix))
    suffix = _dispatch_tuple_text(pr + 1, BASE, other, 1).encode()
    raw = prefix + padding + suffix
    assert len(raw) == limit + len(suffix)
    assert runtime.parse_clawsweeper_dispatch_log(raw[:limit].decode()) == {
        "pr_number": pr,
        "expected_base_sha": BASE,
        "expected_head_sha": HEAD,
        "review_epoch": 0,
    }
    calls: list[str] = []

    def request_once(url: str, headers: dict[str, str], _timeout: float):
        calls.append(url)
        if url.endswith("/logs"):
            assert headers["Authorization"] == "Bearer fixture-token"
            return 302, {
                "Location": (
                    "https://productionresultssa12.blob.core.windows.net/"
                    "actions-results/admission.log?sig=fixture"
                )
            }, b""
        assert "Authorization" not in headers
        return 200, {}, raw

    with tempfile.TemporaryDirectory() as temporary:
        config = config_fixture(Path(temporary))
        prepare_openclaw(config, Path(temporary), pr)
        assert current(config, pr)["review_epoch"] == 0
        mark_dispatched(config, action(config, pr, "clawsweeper.dispatch")["action_id"])
        ingress(
            config, "workflow_run", "oversize-log",
            claw_workflow_payload(run_id, conclusion="failure"),
        )
        client = _admission_log_client(config, request_once)
        assert client.clawsweeper_dispatch_identity(run_id) is None
        outcomes = userland.collect_clawsweeper_terminals(config, client, dry_run=False)
        assert outcomes[0]["result"] == "rail_failure_alerted"
        assert current(config, pr)["state"] == "clawsweeper_queued"
        assert calls[0].endswith("/logs")
        assert "productionresultssa12.blob.core.windows.net" in calls[1]


def test_admission_log_redirect_stays_on_the_artifact_allowlist() -> None:
    head = "a" * 40
    base = "b" * 40
    log = _dispatch_tuple_text(48, base, head, 2).encode()
    blob = (
        "https://productionresultssa7.blob.core.windows.net/"
        "actions-results/admission.log?sig=fixture"
    )
    calls: list[tuple[str, dict[str, str]]] = []

    def allowlisted(url: str, headers: dict[str, str], _timeout: float):
        calls.append((url, dict(headers)))
        if url.endswith("/logs"):
            return 302, {"Location": blob}, b""
        return 200, {}, log

    with tempfile.TemporaryDirectory() as temporary:
        config = config_fixture(Path(temporary))
        client = _admission_log_client(config, allowlisted)
        assert client.clawsweeper_dispatch_identity(36009939846) == {
            "repository": "dinkuskit/blocks",
            "pr_number": 48,
            "base_sha": base,
            "head_sha": head,
            "review_epoch": 2,
        }
    assert calls[0][0].endswith("/logs")
    assert calls[0][1]["Authorization"] == "Bearer fixture-token"
    assert calls[1] == (blob, {
        "Accept": "application/octet-stream",
        "User-Agent": "smoky-review-conductor/1",
    })

    forbidden = (
        "https://results-receiver.actions.githubusercontent.com/logs/job?sig=fixture",
        "https://pipelinesghubeus25.actions.githubusercontent.com/signedlogcontent/4?sig=fixture",
    )
    for location in forbidden:
        seen: list[str] = []

        def denied(url: str, _headers: dict[str, str], _timeout: float, bound: str = location) -> tuple[int, dict[str, str], bytes]:
            seen.append(url)
            return 302, {"Location": bound}, b""

        with tempfile.TemporaryDirectory() as temporary:
            config = config_fixture(Path(temporary))
            client = _admission_log_client(config, denied)
            assert client.clawsweeper_dispatch_identity(36009939846) is None
        assert len(seen) == 1 and seen[0].endswith("/logs")


def _github_client(config: dict[str, Any], transport: Any) -> runtime.GitHubAppClient:
    client = runtime.GitHubAppClient(config, "fixture-key", transport=transport)
    client._token = "fixture-token"
    client._token_expires = 9999999999
    return client


def test_malformed_receipt_does_not_own_a_current_bundle() -> None:
    for index, receipt in enumerate(([], None)):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = config_fixture(root)
            pr, run_id = 93, 4093
            prepare_openclaw(config, root, pr)
            claw_action = action(config, pr, "clawsweeper.dispatch")
            _set_action_status(config, claw_action["action_id"], "dispatched", receipt)
            ingress(config, "workflow_run", f"malformed-{index}", claw_workflow_payload(run_id))
            client = HistoricalThenCurrentGitHub(
                1, run_id, b"", claw_bundle(run_id=run_id, pr=pr), None
            )
            collected = userland.collect_clawsweeper_terminals(config, client, dry_run=False)
            assert collected == [
                {
                    "workflow_run_id": run_id,
                    "result": "historical_bundle_retired",
                    "conclusion": "success",
                }
            ]
            assert current(config, pr)["state"] == "clawsweeper_queued"
            connection = core.open_database(Path(config["paths"]["state_root"]), "dinkuskit/blocks")
            try:
                stored = connection.execute(
                    "SELECT status, verdict FROM rail_workflow_runs WHERE workflow_run_id = ?",
                    (str(run_id),),
                ).fetchone()
                assert stored["status"] == "terminal_attention_required"
                assert stored["verdict"] is None
            finally:
                connection.close()


def test_inflight_no_artifact_failure_stays_pending() -> None:
    cases: list[tuple[str, Any]] = [
        (status, {}) for status in userland.IN_FLIGHT_CLAWSWEEPER_STATUSES
    ]
    cases.append(("dispatching", None))
    for status, receipt in cases:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = config_fixture(root)
            pr, run_id = 94, 4094
            prepare_openclaw(config, root, pr)
            claw_action = action(config, pr, "clawsweeper.dispatch")
            epoch = int(current(config, pr)["review_epoch"])
            _set_action_status(config, claw_action["action_id"], status, receipt)
            ingress(
                config, "workflow_run", f"inflight-failure-{status}",
                claw_workflow_payload(run_id, conclusion="failure"),
            )
            identity = {
                "repository": "dinkuskit/blocks",
                "pr_number": pr,
                "base_sha": BASE,
                "head_sha": HEAD,
                "review_epoch": epoch,
            }

            class ProvenFailure:
                def list_run_artifacts(self, _run_id: int) -> list[dict[str, Any]]:
                    return []

                def clawsweeper_dispatch_identity(self, _run_id: int) -> dict[str, Any]:
                    return identity

            collected = userland.collect_clawsweeper_terminals(
                config, ProvenFailure(), dry_run=False
            )
            assert collected == [
                {
                    "workflow_run_id": run_id,
                    "result": "current_dispatch_pending",
                    "conclusion": "failure",
                }
            ]
            assert current(config, pr)["state"] == "clawsweeper_queued"
            connection = core.open_database(Path(config["paths"]["state_root"]), "dinkuskit/blocks")
            try:
                stored = connection.execute(
                    "SELECT status, verdict, bound_pr_number FROM rail_workflow_runs WHERE workflow_run_id = ?",
                    (str(run_id),),
                ).fetchone()
                assert stored["status"] == "terminal_pending_verdict_bridge"
                assert stored["verdict"] is None and stored["bound_pr_number"] is None
            finally:
                connection.close()
            notifier = FakeNotifier()
            userland.deliver_notifications(config, notifier, dry_run=False)
            assert notifier.sent == []
            mark_dispatched(config, claw_action["action_id"])
            again = userland.collect_clawsweeper_terminals(config, ProvenFailure(), dry_run=False)
            assert again[0]["result"] == "bound_execution_failed"
            assert current(config, pr)["state"] == "clawsweeper_failed"


def test_inflight_receipt_for_a_different_run_still_retires() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        config = config_fixture(root)
        pr, run_id = 95, 4095
        prepare_openclaw(config, root, pr)
        claw_action = action(config, pr, "clawsweeper.dispatch")
        epoch = int(current(config, pr)["review_epoch"])
        _set_action_status(
            config, claw_action["action_id"], "dispatching", {"workflow_run_id": run_id + 9}
        )
        ingress(
            config, "workflow_run", "other-run",
            claw_workflow_payload(run_id, conclusion="failure"),
        )
        identity = {
            "repository": "dinkuskit/blocks",
            "pr_number": pr,
            "base_sha": BASE,
            "head_sha": HEAD,
            "review_epoch": epoch,
        }

        class ProvenFailure:
            def list_run_artifacts(self, _run_id: int) -> list[dict[str, Any]]:
                return []

            def clawsweeper_dispatch_identity(self, _run_id: int) -> dict[str, Any]:
                return identity

        collected = userland.collect_clawsweeper_terminals(config, ProvenFailure(), dry_run=False)
        assert collected[0]["result"] == "rail_failure_alerted"
        assert current(config, pr)["state"] == "clawsweeper_queued"
        connection = core.open_database(Path(config["paths"]["state_root"]), "dinkuskit/blocks")
        try:
            stored = connection.execute(
                "SELECT status, bound_pr_number FROM rail_workflow_runs WHERE workflow_run_id = ?",
                (str(run_id),),
            ).fetchone()
            assert stored["status"] == "terminal_attention_required"
            assert stored["bound_pr_number"] is None
        finally:
            connection.close()


def _jobs_page_transport(pages: dict[int, tuple[dict[str, Any], str | None]], log: bytes):
    def transport(
        _method: str, url: str, _headers: dict[str, str], _body: bytes | None, _timeout: float
    ):
        if _is_jobs_url(url):
            payload, nxt = pages[_jobs_page_number(url)]
            headers = {}
            if nxt is not None:
                headers["Link"] = f'<{nxt}>; rel="next"'
            return 200, headers, json.dumps(payload).encode()
        if url.endswith("/logs"):
            return 200, {}, log
        raise AssertionError(url)

    return transport


def test_admission_job_on_a_later_page_is_the_dispatch_identity() -> None:
    head = "a" * 40
    base = "b" * 40
    log = _dispatch_tuple_text(48, base, head, 2).encode()
    admission = {"id": 7, "name": "Run pinned exact-tuple ClawSweeper / Admit exact-tuple PR"}
    other = {"id": 8, "name": "Native ClawSweeper review"}
    with tempfile.TemporaryDirectory() as temporary:
        config = config_fixture(Path(temporary))
        run_url = (
            "https://api.github.com/repos/dinkuskit/blocks/actions/runs/36009939846/jobs"
        )
        page2 = run_url + "?per_page=100&page=2"
        client = _github_client(
            config,
            _jobs_page_transport(
                {
                    1: ({"total_count": 2, "jobs": [other]}, page2),
                    2: ({"total_count": 2, "jobs": [admission]}, None),
                },
                log,
            ),
        )
        assert client.clawsweeper_dispatch_identity(36009939846) == {
            "repository": "dinkuskit/blocks",
            "pr_number": 48,
            "base_sha": base,
            "head_sha": head,
            "review_epoch": 2,
        }
        duplicate = _github_client(
            config,
            _jobs_page_transport(
                {
                    1: ({"total_count": 2, "jobs": [admission]}, page2),
                    2: ({"total_count": 2, "jobs": [admission]}, None),
                },
                log,
            ),
        )
        assert duplicate.clawsweeper_dispatch_identity(36009939846) is None


def test_admission_job_lookup_does_not_read_past_the_page_cap() -> None:
    fetched: list[int] = []

    def transport(
        _method: str, url: str, _headers: dict[str, str], _body: bytes | None, _timeout: float
    ):
        assert _is_jobs_url(url)
        page = _jobs_page_number(url)
        fetched.append(page)
        base = url.split("?", 1)[0]
        if page <= runtime.MAX_LIST_PAGES:
            nxt = f"{base}?per_page=100&page={page + 1}"
            payload = {"total_count": runtime.MAX_LIST_PAGES + 1, "jobs": [{"id": page, "name": "other"}]}
            return 200, {"Link": f'<{nxt}>; rel="next"'}, json.dumps(payload).encode()
        payload = {
            "total_count": runtime.MAX_LIST_PAGES + 1,
            "jobs": [{"id": 7, "name": "Admit exact-tuple"}],
        }
        return 200, {}, json.dumps(payload).encode()

    with tempfile.TemporaryDirectory() as temporary:
        config = config_fixture(Path(temporary))
        client = _github_client(config, transport)
        try:
            client.clawsweeper_dispatch_identity(36009939846)
        except runtime.GitHubApiError as exc:
            assert "bounded page count" in str(exc)
        else:
            raise AssertionError("job lookup read past the page cap")
        assert fetched == list(range(1, runtime.MAX_LIST_PAGES + 1))


def test_string_receipt_run_id_is_not_a_stored_identity() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        config = config_fixture(root)
        pr, run_id = 96, 4096
        prepare_openclaw(config, root, pr)
        claw_action = action(config, pr, "clawsweeper.dispatch")
        _set_action_status(
            config, claw_action["action_id"], "dispatched", {"workflow_run_id": str(run_id)}
        )
        ingress(
            config, "workflow_run", "string-run",
            claw_workflow_payload(run_id, conclusion="failure"),
        )
        connection = core.open_database(Path(config["paths"]["state_root"]), "dinkuskit/blocks")
        try:
            rail_run = connection.execute(
                "SELECT * FROM rail_workflow_runs WHERE workflow_run_id = ?",
                (str(run_id),),
            ).fetchone()
        finally:
            connection.close()
        assert userland.proven_no_artifact_dispatch_identity(
            config, EmptyGitHub(), rail_run
        ) is None


def test_string_receipt_run_id_does_not_pass_the_admission_fence() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        config = config_fixture(root)
        pr, run_id = 97, 4097
        prepare_openclaw(config, root, pr)
        claw_action = action(config, pr, "clawsweeper.dispatch")
        epoch = int(current(config, pr)["review_epoch"])
        _set_action_status(
            config, claw_action["action_id"], "dispatched", {"workflow_run_id": str(run_id)}
        )
        ingress(
            config, "workflow_run", "string-fence",
            claw_workflow_payload(run_id, conclusion="failure"),
        )
        identity = {
            "repository": "dinkuskit/blocks",
            "pr_number": pr,
            "base_sha": BASE,
            "head_sha": HEAD,
            "review_epoch": epoch,
        }

        class ProvenFailure:
            def list_run_artifacts(self, _run_id: int) -> list[dict[str, Any]]:
                return []

            def clawsweeper_dispatch_identity(self, _run_id: int) -> dict[str, Any]:
                return identity

        collected = userland.collect_clawsweeper_terminals(config, ProvenFailure(), dry_run=False)
        assert collected[0]["result"] == "rail_failure_alerted"
        assert current(config, pr)["state"] == "clawsweeper_queued"


def test_malformed_admission_job_id_stays_pending_and_collection_continues() -> None:
    bad_run, later_run = 4098, 4099

    def transport(
        _method: str, url: str, _headers: dict[str, str], _body: bytes | None, _timeout: float
    ):
        if url.endswith("/artifacts"):
            return 200, {}, b'{"artifacts":[]}'
        if _is_jobs_url(url):
            if f"/runs/{bad_run}/" in url:
                payload = {"jobs": [{"id": None, "name": "Admit exact-tuple"}]}
            else:
                payload = {"jobs": [{"id": 8, "name": "Native ClawSweeper review"}]}
            return 200, {}, json.dumps(payload).encode()
        raise AssertionError(url)

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        config = config_fixture(root)
        pr = 98
        prepare_openclaw(config, root, pr)
        mark_dispatched(config, action(config, pr, "clawsweeper.dispatch")["action_id"])
        first = claw_workflow_payload(bad_run, conclusion="failure")
        first["workflow_run"]["created_at"] = "2026-09-24T14:00:00Z"
        second = claw_workflow_payload(later_run, conclusion="failure")
        second["workflow_run"]["created_at"] = "2026-09-24T14:05:00Z"
        ingress(config, "workflow_run", "bad-job-id", first)
        ingress(config, "workflow_run", "later-failure", second)
        client = _github_client(config, transport)
        try:
            client.clawsweeper_dispatch_identity(bad_run)
        except runtime.GitHubApiError as exc:
            assert "admission job id" in str(exc)
        else:
            raise AssertionError("malformed admission job id was treated as absent")
        collected = userland.collect_clawsweeper_terminals(config, client, dry_run=False)
        assert [item["result"] for item in collected] == [
            "dispatch_identity_unavailable",
            "rail_failure_alerted",
        ]
        connection = core.open_database(Path(config["paths"]["state_root"]), "dinkuskit/blocks")
        try:
            pending = connection.execute(
                "SELECT status FROM rail_workflow_runs WHERE workflow_run_id = ?",
                (str(bad_run),),
            ).fetchone()
            later = connection.execute(
                "SELECT status FROM rail_workflow_runs WHERE workflow_run_id = ?",
                (str(later_run),),
            ).fetchone()
            assert pending["status"] == "terminal_pending_verdict_bridge"
            assert later["status"] == "terminal_attention_required"
        finally:
            connection.close()
        assert current(config, pr)["state"] == "clawsweeper_queued"


def test_overlong_bundle_epoch_does_not_abort_later_collection() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        config = config_fixture(root)
        pr = 99
        bad_run, good_run = 4100, 4101
        prepare_openclaw(config, root, pr)
        mark_dispatched(config, action(config, pr, "clawsweeper.dispatch")["action_id"])
        bad_payload = claw_workflow_payload(bad_run)
        bad_payload["workflow_run"]["created_at"] = "2026-09-24T14:00:00Z"
        good_payload = claw_workflow_payload(good_run)
        good_payload["workflow_run"]["created_at"] = "2026-09-24T14:05:00Z"
        ingress(config, "workflow_run", "overlong-epoch", bad_payload)
        ingress(config, "workflow_run", "current-clean", good_payload)

        class TwoBundles:
            def list_run_artifacts(self, run_id: int) -> list[dict[str, Any]]:
                return [{
                    "id": run_id,
                    "name": f"dinkuskit-native-review-{run_id}-1",
                    "expired": False,
                }]

            def download_artifact(self, artifact_id: int) -> bytes:
                if artifact_id == bad_run:
                    # One past SQLite's signed 64-bit integer. An unbounded
                    # digit check would bind this and abort the later receipt.
                    return claw_bundle(run_id=bad_run, pr=pr, review_epoch=2**63)
                return claw_bundle(run_id=good_run, pr=pr)

        collected = userland.collect_clawsweeper_terminals(config, TwoBundles(), dry_run=False)
        assert [item["result"] for item in collected] == [
            "bundle_identity_unavailable",
            "terminal_materialized",
        ]
        assert collected[1]["verdict"] == "clean"
        connection = core.open_database(Path(config["paths"]["state_root"]), "dinkuskit/blocks")
        try:
            pending = connection.execute(
                "SELECT status FROM rail_workflow_runs WHERE workflow_run_id = ?",
                (str(bad_run),),
            ).fetchone()
            assert pending["status"] == "terminal_pending_verdict_bridge"
        finally:
            connection.close()


def test_dispatch_stores_only_documented_workflow_run_id() -> None:
    head = "a" * 40
    base = "b" * 40
    recorded: list[dict[str, Any]] = []
    scripted: list[tuple[int, bytes]] = [
        (200, json.dumps({
            "workflow_run_id": 99,
            "run_url": "https://api.github.com/repos/dinkuskit/blocks/actions/runs/99",
            "html_url": "https://github.com/dinkuskit/blocks/actions/runs/99",
        }).encode()),
        (204, b""),
        (200, json.dumps({
            "id": 99,
            "run_url": "https://api.github.com/repos/dinkuskit/blocks/actions/runs/99",
            "html_url": "https://github.com/dinkuskit/blocks/actions/runs/99",
        }).encode()),
    ]

    def transport(
        _method: str, url: str, _headers: dict[str, str], body: bytes | None, _timeout: float
    ):
        assert url.endswith("/dispatches")
        assert body is not None
        payload = json.loads(body)
        recorded.append(payload)
        status, raw = scripted.pop(0)
        return status, {}, raw

    with tempfile.TemporaryDirectory() as temporary:
        config = config_fixture(Path(temporary))
        client = runtime.GitHubAppClient(config, "fixture-key", transport=transport)
        client._token = "fixture-token"
        client._token_expires = 9999999999
        assert client.dispatch_clawsweeper(
            pr_number=48, base_sha=base, head_sha=head, publish=True
        ) == 99
        assert client.dispatch_clawsweeper(
            pr_number=48, base_sha=base, head_sha=head, publish=True
        ) is None
        assert client.dispatch_clawsweeper(
            pr_number=48, base_sha=base, head_sha=head, publish=True
        ) is None
    assert scripted == []
    assert len(recorded) == 3
    for payload in recorded:
        assert payload["return_run_details"] is True
        assert payload["inputs"]["expected_base_sha"] == base
        assert payload["inputs"]["expected_head_sha"] == head


def test_failed_clawsweeper_publisher_binds_exact_artifact_and_fails_pr() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        config = config_fixture(root)
        pr = 77
        run_id = 2077
        prepare_openclaw(config, root, pr)
        claw_action = action(config, pr, "clawsweeper.dispatch")
        mark_dispatched(config, claw_action["action_id"])
        ingress(
            config,
            "workflow_run",
            f"claw-publication-failure-{run_id}",
            claw_workflow_payload(run_id, conclusion="failure"),
        )
        client = FakeGitHub(run_id, claw_bundle(run_id=run_id, pr=pr))
        collected = userland.collect_clawsweeper_terminals(
            config,
            client,
            dry_run=False,
        )
        assert collected == [
            {
                "workflow_run_id": run_id,
                "result": "terminal_failure_materialized",
                "conclusion": "failure",
                "pr_number": pr,
                "head_sha": HEAD,
            }
        ]
        runtime.drain_bridge_inboxes(config)
        assert current(config, pr)["state"] == "clawsweeper_failed"
        notifier = FakeNotifier()
        userland.deliver_notifications(config, notifier, dry_run=False)
        row = current(config, pr)
        assert_exact_terminal_messages(notifier, concise_blocked(pr, row["blocker"]))


def test_notification_commands_use_gateway_without_secret_flags() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        config = config_fixture(Path(temporary))
        notifier = userland.OpenClawNotifier(
            config,
            environment={
                "SMOKY_REVIEW_CONDUCTOR_DISCORD_TARGET": "channel:fixture",
                "SMOKY_REVIEW_CONDUCTOR_SIGNAL_TARGET": "+15555550123",
            },
        )
        discord = notifier.command("discord", "fixture message")
        context = notifier.command("openclaw_context", "fixture message")
        assert discord[:3] == [
            config["notifications"]["openclaw_path"],
            "message",
            "send",
        ]
        assert "--channel" in discord and "discord" in discord
        assert "--target" in discord and "channel:fixture" in discord
        assert context[1:3] == ["system", "event"]
        assert "agent:main:review-conductor" in context
        assert not any("token" in item.lower() or "password" in item.lower() for item in discord + context)


def test_pretty_multiline_notification_receipt_and_explicit_reconciliation() -> None:
    assert userland.parse_json_receipt(
        json.dumps({"result": "sent", "receipt": {"id": "fixture"}}, indent=2),
        "fixture notification",
    ) == {"result": "sent", "receipt": {"id": "fixture"}}
    with tempfile.TemporaryDirectory() as temporary:
        config = config_fixture(Path(temporary))
        pr = 80
        ingress(config, "pull_request", "notification-pr", pr_payload(pr))
        ingress(
            config,
            "workflow_run",
            "notification-ci",
            ci_payload(pr, 1080, conclusion="failure"),
        )
        userland.deliver_notifications(config, UncertainNotifier(), dry_run=False)

        connection = core.open_database(Path(config["paths"]["state_root"]))
        try:
            rows = connection.execute(
                "SELECT channel, status, attempts FROM notification_deliveries ORDER BY channel"
            ).fetchall()
            assert [(row["channel"], row["status"], row["attempts"]) for row in rows] == [
                ("discord", "uncertain", 1),
                ("openclaw_context", "uncertain", 1),
                ("signal", "uncertain", 1),
            ]
        finally:
            connection.close()

        planned = userland.reconcile_uncertain_notification(
            config,
            pr,
            "discord",
            "sent",
            "provider-delivery-observed",
            apply=False,
        )
        assert planned["result"] == "planned"
        try:
            userland.reconcile_uncertain_notification(
                config,
                pr,
                "signal",
                "retry",
                "provider-delivery-observed",
                apply=True,
            )
        except userland.UserlandError as exc:
            assert "confirmation is invalid" in str(exc)
        else:
            raise AssertionError("mismatched notification confirmation must fail closed")

        sent = userland.reconcile_uncertain_notification(
            config,
            pr,
            "discord",
            "sent",
            "provider-delivery-observed",
            apply=True,
        )
        retry = userland.reconcile_uncertain_notification(
            config,
            pr,
            "signal",
            "retry",
            "provider-nondelivery-observed",
            apply=True,
        )
        assert sent["result"] == retry["result"] == "reconciled"
        assert sent["merge_dispatched"] is retry["merge_dispatched"] is False

        notifier = FakeNotifier()
        delivered = userland.deliver_notifications(config, notifier, dry_run=False)
        assert [row["channel"] for row in delivered["deliveries"]] == ["signal"]
        assert [channel for channel, _message in notifier.sent] == ["signal"]
        connection = core.open_database(Path(config["paths"]["state_root"]))
        try:
            states = {
                row["channel"]: (row["status"], row["attempts"])
                for row in connection.execute(
                    "SELECT channel, status, attempts FROM notification_deliveries"
                ).fetchall()
            }
            assert states == {
                "discord": ("sent", 1),
                "openclaw_context": ("uncertain", 1),
                "signal": ("sent", 2),
            }
            reconciliations = connection.execute(
                "SELECT channel, disposition, prior_attempts FROM notification_reconciliations ORDER BY channel"
            ).fetchall()
            assert [tuple(row) for row in reconciliations] == [
                ("discord", "sent", 1),
                ("signal", "retry", 1),
            ]
        finally:
            connection.close()


def test_attended_bootstrap_rejects_echoing_getpass_fallback() -> None:
    saved = launcher.getpass.getpass

    def fallback(_prompt: str) -> str:
        warnings.warn("terminal echo could not be disabled", launcher.getpass.GetPassWarning)
        return "echoed-fixture"

    launcher.getpass.getpass = fallback
    try:
        try:
            launcher.read_attended_token("fixture: ")
        except launcher.LauncherError as exc:
            assert "secure no-echo terminal input is unavailable" in str(exc)
        else:
            raise AssertionError("echoing getpass fallback must fail closed")
    finally:
        launcher.getpass.getpass = saved


class HydrationRunner:
    def __init__(self, checkout: Path) -> None:
        self.checkout = checkout
        self.calls: list[list[str]] = []
        self.fetched = False

    def __call__(self, command: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append(command)
        arguments = command[command.index("-C") + 2 :]
        stdout = ""
        returncode = 0
        if arguments == ["rev-parse", "--show-toplevel"]:
            stdout = str(self.checkout) + "\n"
        elif arguments == ["remote", "get-url", "origin"]:
            stdout = "https://github.com/dinkuskit/blocks.git\n"
        elif arguments == ["status", "--porcelain"]:
            stdout = ""
        elif arguments == ["rev-parse", "--verify", "HEAD^{commit}"]:
            stdout = BASE + "\n"
        elif arguments == ["for-each-ref", "--format=%(refname)%09%(objectname)"]:
            stdout = f"refs/heads/main\t{BASE}\n"
        elif arguments == ["cat-file", "-e", f"{BASE}^{{commit}}"]:
            returncode = 0
        elif arguments == ["cat-file", "-e", f"{HEAD}^{{commit}}"]:
            returncode = 0 if self.fetched else 1
        elif arguments[:5] == [
            "fetch",
            "--no-tags",
            "--no-recurse-submodules",
            "--no-write-fetch-head",
            "https://github.com/dinkuskit/blocks.git",
        ]:
            assert arguments[5] == "refs/pull/78/head"
            self.fetched = True
        elif arguments == ["rev-parse", "--verify", f"{HEAD}^{{commit}}"]:
            assert self.fetched
            stdout = HEAD + "\n"
        elif arguments == ["merge-base", "--is-ancestor", BASE, HEAD]:
            assert self.fetched
        else:
            raise AssertionError(f"unexpected hydration command: {arguments}")
        return subprocess.CompletedProcess(command, returncode, stdout=stdout, stderr="")


def test_missing_pr_head_requires_service_owned_authentication() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        config = config_fixture(Path(temporary))
        pr = 78
        ingress(config, "pull_request", "hydrate-pr", pr_payload(pr))
        ingress(config, "workflow_run", "hydrate-ci", ci_payload(pr, 1078))
        queued = action(config, pr, "openclaw.enqueue")
        checkout = Path(config["paths"]["blocks_checkout"])
        runner = HydrationRunner(checkout)
        try:
            userland.hydrate_exact_pr_head(config, queued, runner=runner)
        except userland.HydrationFailure as exc:
            assert exc.reason == "service_auth_unavailable"
        else:
            raise AssertionError("missing auth must not use an unauthenticated fallback")
        assert not any("fetch" in call for call in runner.calls)



def test_adapter_failure_stops_retry_loop_notifies_and_allows_explicit_retry() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        config = config_fixture(root)
        pr = 79
        ingress(config, "pull_request", "adapter-pr", pr_payload(pr))
        ingress(config, "workflow_run", "adapter-ci", ci_payload(pr, 1079))
        failing = root / "failing-smoky"
        failing.write_text("#!/bin/sh\nexit 2\n", encoding="utf-8")
        failing.chmod(0o755)
        config["spark"]["smoky_path"] = str(failing)

        first = runtime.drain_actions(config, None, dry_run=False)
        assert first["actions"] == [
            {
                "action_id": action(config, pr, "openclaw.enqueue")["action_id"],
                "kind": "openclaw.enqueue",
                "result": "failed",
            }
        ]
        assert current(config, pr)["state"] == "openclaw_failed"
        failed = action(config, pr, "openclaw.enqueue")
        assert failed["status"] == "failed" and failed["attempts"] == 1
        second = runtime.drain_actions(config, None, dry_run=False)
        assert second["actions"] == []
        assert action(config, pr, "openclaw.enqueue")["attempts"] == 1

        notifier = FakeNotifier()
        userland.deliver_notifications(config, notifier, dry_run=False)
        row = current(config, pr)
        assert_exact_terminal_messages(notifier, concise_blocked(pr, row["blocker"]))

        planned = userland.retry_failed_openclaw(config, pr, apply=False)
        assert planned["result"] == "planned"
        retried = userland.retry_failed_openclaw(config, pr, apply=True)
        assert retried["result"] == "retried"
        assert current(config, pr)["state"] == "openclaw_queued"
        assert action(config, pr, "openclaw.enqueue")["status"] == "pending"

        runtime.drain_actions(config, None, dry_run=False)
        assert current(config, pr)["state"] == "openclaw_failed"
        assert action(config, pr, "openclaw.enqueue")["attempts"] == 2
        userland.deliver_notifications(config, notifier, dry_run=False)
        assert len(notifier.sent) == 6


def main() -> None:
    tests = [
        test_userland_config_has_no_root_or_custom_identity_route,
        test_one_attended_three_paste_bootstrap_writes_exact_vault_domains,
        test_bootstrap_rejects_wrong_service_account_scope_before_writing,
        test_supervisor_passes_values_only_by_descriptor_without_root_or_op_state,
        test_launcher_main_passes_the_exact_selected_config_to_start,
        test_openclaw_human_gate_is_terminal_without_dispatching_clawsweeper,
        test_ci_failure_alerts_without_starting_either_review_rail,
        test_real_spark_receipt_shape_bridges_nonzero_human_gate,
        test_legacy_openclaw_terminal_without_review_policy_does_not_require_applied_p3,
        test_exact_artifacts_drive_ready_notification_once_without_merge,
        test_keep_open_without_defects_is_review_success_not_merge,
        test_clawsweeper_finding_waits_for_adjudication_without_notification,
        test_first_round_openclaw_findings_and_repair_required_are_silent,
        test_waiting_human_missing_rail_queues_blocked_notification_once,
        test_unknown_head_outcome_fail_closed_routes_blocked_notification,
        test_broken_enrollment_fail_closed_routes_blocked_notification,
        test_closed_heads_reject_direct_and_service_loop_dispatch,
        test_closed_heads_stay_silent_when_trusted_enrollment_is_broken,
        test_run_tick_suppresses_review_stages_unless_conductor_enrolled,
        test_pending_notifications_are_revalidated_before_send,
        test_stale_decision_identity_retires_and_new_blocked_delivers_once,
        test_webhook_state_change_between_claim_and_send_retires_stale_notification,
        test_webhook_state_change_after_final_predicate_retires_stale_notification,
        test_persisted_string_and_malformed_quality_flags_fail_closed_on_the_queue,
        test_enrollment_route_change_at_reserved_send_retires_stale_notification,
        test_legacy_pending_rows_without_decision_identity_do_not_duplicate_delivery,
        test_malformed_persisted_rail_token_notifies_blocked_once,
        test_complete_event_identity_prevents_retired_keys_from_suppressing_current,
        test_retry_attempt_revalidation_sends_only_the_current_event_key,
        test_invalid_tuple_persisted_rows_fail_closed_without_review_dispatch,
        test_unenrolled_inconsistent_tuple_notifies_blocked_without_dispatch,
        test_run_tick_uses_trusted_enrollment_not_caller_payloads,
        test_fail_closed_canonical_reason_beats_stale_blocker_text,
        test_sub_platinum_or_insufficient_proof_stops_at_human_gate,
        test_completed_clawsweeper_human_gate_owner_adjudication_projects_ready,
        test_unbound_clawsweeper_workflow_failure_alerts_without_guessing_a_pr,
        test_bound_failed_workflow_without_bundle_is_execution_failure,
        test_historical_bundle_does_not_block_current_no_artifact_failure,
        test_historical_success_bundle_still_allows_the_current_clean_terminal,
        test_inflight_dispatch_bundle_stays_pending,
        test_reopened_epoch_retires_previous_run_and_keeps_current_bundle,
        test_ambiguous_current_dispatches_stay_pending,
        test_bundle_retirement_mutants_fail_their_intended_tests,
        test_unproven_no_artifact_failure_does_not_choose_a_pull_request,
        test_stored_dispatch_run_id_fails_queued_execution_without_a_log,
        test_dispatch_identity_transport_failure_leaves_the_run_pending,
        test_parse_clawsweeper_dispatch_log_accepts_one_consistent_tuple,
        test_admission_job_log_is_the_only_dispatch_identity_source,
        test_malformed_receipt_does_not_own_a_current_bundle,
        test_inflight_no_artifact_failure_stays_pending,
        test_inflight_receipt_for_a_different_run_still_retires,
        test_admission_job_on_a_later_page_is_the_dispatch_identity,
        test_admission_job_lookup_does_not_read_past_the_page_cap,
        test_string_receipt_run_id_is_not_a_stored_identity,
        test_string_receipt_run_id_does_not_pass_the_admission_fence,
        test_malformed_admission_job_id_stays_pending_and_collection_continues,
        test_overlong_bundle_epoch_does_not_abort_later_collection,
        test_oversize_admission_log_with_conflicting_suffix_is_not_an_identity,
        test_admission_log_redirect_stays_on_the_artifact_allowlist,
        test_dispatch_stores_only_documented_workflow_run_id,
        test_failed_clawsweeper_publisher_binds_exact_artifact_and_fails_pr,
        test_notification_commands_use_gateway_without_secret_flags,
        test_pretty_multiline_notification_receipt_and_explicit_reconciliation,
        test_attended_bootstrap_rejects_echoing_getpass_fallback,
        test_missing_pr_head_requires_service_owned_authentication,
        test_adapter_failure_stops_retry_loop_notifies_and_allows_explicit_retry,
    ]
    for test in tests:
        test()
    print(f"review conductor userland tests passed ({len(tests)})")


if __name__ == "__main__":
    main()
