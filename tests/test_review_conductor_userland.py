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
import subprocess
import sys
import tempfile
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
    run = root / "spark" / request_id
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
    return run


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
            f"main_sha: {BASE}",
            f"pull_head_sha: {HEAD}",
            f"pr_rating_overall: {overall}",
            f"pr_rating_proof: {proof}",
            f"real_behavior_proof_status: {proof_status}",
            "maintainer_decision: "
            + json.dumps(
                {
                    "required": maintainer_required,
                    "kind": "manual_review" if maintainer_required else "none",
                },
                separators=(",", ":"),
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
            launcher.CAPABILITIES[1]: b"-----BEGIN PRIVATE KEY-----\nfixture\n-----END PRIVATE KEY-----",
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
        assert [channel for channel, _message in notifier.sent] == [
            "openclaw_context",
            "discord",
            "signal",
        ]
        assert all("ci_failed" in message for _channel, message in notifier.sent)
        assert all("No merge was attempted" in message for _channel, message in notifier.sent)


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
        assert [channel for channel, _message in notifier.sent] == [
            "openclaw_context",
            "discord",
            "signal",
        ]
        assert all("No merge was attempted" in message for _channel, message in notifier.sent)
        assert all("overall tier B" in message for _channel, message in notifier.sent)
        assert all("proof sufficient" in message for _channel, message in notifier.sent)
        assert len(first["deliveries"]) == 3
        second = userland.deliver_notifications(config, notifier, dry_run=False)
        assert second["deliveries"] == []
        assert len(notifier.sent) == 3
        assert current(config, pr)["state"] == "ready_for_human_merge"


def test_clawsweeper_finding_waits_for_adjudication_and_notifies_discord_only() -> None:
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
        userland.deliver_notifications(config, notifier, dry_run=False)
        assert [channel for channel, _message in notifier.sent] == [
            "openclaw_context",
            "discord",
        ]
        assert all("awaiting bounded adjudication" in message for _channel, message in notifier.sent)


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
        assert [channel for channel, _message in notifier.sent] == [
            "openclaw_context",
            "discord",
            "signal",
        ]
        assert all("No merge was attempted" in message for _channel, message in notifier.sent)


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
        assert current(config, pr)["state"] == "clawsweeper_queued"
        notifier = FakeNotifier()
        userland.deliver_notifications(config, notifier, dry_run=False)
        assert [channel for channel, _message in notifier.sent] == [
            "openclaw_context",
            "discord",
            "signal",
        ]
        assert all("No PR was marked ready" in message for _channel, message in notifier.sent)
        assert all("No merge was attempted" in message for _channel, message in notifier.sent)


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
        assert [channel for channel, _message in notifier.sent] == [
            "openclaw_context",
            "discord",
            "signal",
        ]
        assert all("clawsweeper_failed" in message for _channel, message in notifier.sent)
        assert all("No merge was attempted" in message for _channel, message in notifier.sent)


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


def test_missing_pr_head_is_fetched_without_ref_or_worktree_mutation() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        config = config_fixture(Path(temporary))
        pr = 78
        ingress(config, "pull_request", "hydrate-pr", pr_payload(pr))
        ingress(config, "workflow_run", "hydrate-ci", ci_payload(pr, 1078))
        queued = action(config, pr, "openclaw.enqueue")
        checkout = Path(config["paths"]["blocks_checkout"])
        runner = HydrationRunner(checkout)
        result = userland.hydrate_exact_pr_head(config, queued, runner=runner)
        assert result["result"] == "fetched"
        assert result["refs_changed"] is False
        assert result["worktree_changed"] is False
        fetches = [call for call in runner.calls if "fetch" in call]
        assert len(fetches) == 1
        assert "--no-write-fetch-head" in fetches[0]
        assert "refs/pull/78/head" in fetches[0]


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
        assert [channel for channel, _message in notifier.sent] == [
            "openclaw_context",
            "discord",
            "signal",
        ]
        assert all("openclaw_failed" in message for _channel, message in notifier.sent)

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
        test_exact_artifacts_drive_ready_notification_once_without_merge,
        test_clawsweeper_finding_waits_for_adjudication_and_notifies_discord_only,
        test_sub_platinum_or_insufficient_proof_stops_at_human_gate,
        test_unbound_clawsweeper_workflow_failure_alerts_without_guessing_a_pr,
        test_failed_clawsweeper_publisher_binds_exact_artifact_and_fails_pr,
        test_notification_commands_use_gateway_without_secret_flags,
        test_pretty_multiline_notification_receipt_and_explicit_reconciliation,
        test_attended_bootstrap_rejects_echoing_getpass_fallback,
        test_missing_pr_head_is_fetched_without_ref_or_worktree_mutation,
        test_adapter_failure_stops_retry_loop_notifies_and_allows_explicit_retry,
    ]
    for test in tests:
        test()
    print(f"review conductor userland tests passed ({len(tests)})")


if __name__ == "__main__":
    main()
