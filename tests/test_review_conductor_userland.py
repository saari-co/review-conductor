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
        test_persisted_string_and_malformed_quality_flags_fail_closed_on_the_queue,
        test_invalid_tuple_persisted_rows_fail_closed_without_review_dispatch,
        test_unenrolled_inconsistent_tuple_notifies_blocked_without_dispatch,
        test_run_tick_uses_trusted_enrollment_not_caller_payloads,
        test_fail_closed_canonical_reason_beats_stale_blocker_text,
        test_sub_platinum_or_insufficient_proof_stops_at_human_gate,
        test_completed_clawsweeper_human_gate_owner_adjudication_projects_ready,
        test_unbound_clawsweeper_workflow_failure_alerts_without_guessing_a_pr,
        test_bound_failed_workflow_without_bundle_is_execution_failure,
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
