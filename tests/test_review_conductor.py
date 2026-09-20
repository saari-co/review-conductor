#!/usr/bin/env python3
"""Isolated integration proof for the event-driven Review Conductor."""

from __future__ import annotations

import hashlib
import hmac
import importlib.util
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "tools" / "review_conductor.py"
CONFIG = ROOT / "contracts" / "review-conductor" / "dinkuskit-blocks.json"
SECRET = "review-conductor-fixture-secret"
BASE = "1" * 40


def run(*args: str, env: dict[str, str] | None = None, expected: int = 0) -> dict:
    command_env = os.environ.copy()
    command_env["SMOKY_REVIEW_CONDUCTOR_WEBHOOK_SECRET"] = SECRET
    if env:
        command_env.update(env)
    command_args = list(args)
    if command_args and command_args[0] == "reconcile-workflow-run":
        body_index = command_args.index("--body-file") + 1
        body_path = Path(command_args[body_index])
        command_args.extend(("--signature", signature(body_path)))
    result = subprocess.run(
        [sys.executable, str(CORE), *command_args],
        cwd=ROOT,
        env=command_env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=30,
    )
    assert result.returncode == expected, (
        f"expected rc={expected}, got {result.returncode}\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
    if expected != 0:
        return {"stderr": result.stderr}
    return json.loads(result.stdout)


def write_json(root: Path, name: str, value: dict) -> Path:
    path = root / name
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
    return path


def signature(path: Path) -> str:
    body = path.read_bytes()
    return "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


def pr_payload(
    pr: int,
    head: str,
    action: str = "opened",
    author: str = "alice",
    base_ref: str = "main",
    updated_at: str | None = None,
) -> dict:
    if updated_at is None:
        updated_at = f"2026-08-27T00:00:{int(head[0], 16):02d}Z"
    return {
        "action": action,
        "repository": {"id": 1306882611, "full_name": "dinkuskit/blocks"},
        "pull_request": {
            "number": pr,
            "base": {"ref": base_ref, "sha": BASE},
            "head": {"sha": head},
            "user": {"login": author},
            "updated_at": updated_at,
            "merged": False,
        },
    }


def workflow_payload(
    pr: int,
    head: str,
    conclusion: str,
    run_id: int,
    base_ref: str = "main",
    created_at: str = "2026-08-27T00:10:00Z",
) -> dict:
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
                {
                    "number": pr,
                    "base": {"ref": base_ref, "sha": BASE},
                    "head": {"sha": head},
                }
            ],
        },
    }


def github_event(
    temp: Path,
    state: Path,
    event_type: str,
    delivery: str,
    payload: dict,
) -> dict:
    path = write_json(temp, f"{delivery}.json", payload)
    return run(
        "github-event",
        "--config",
        str(CONFIG),
        "--state-root",
        str(state),
        "--event-type",
        event_type,
        "--delivery-id",
        delivery,
        "--signature",
        signature(path),
        "--body-file",
        str(path),
    )


def internal_event(temp: Path, state: Path, name: str, payload: dict, expected: int = 0) -> dict:
    path = write_json(temp, f"{name}.json", payload)
    return run(
        "internal-event",
        "--config",
        str(CONFIG),
        "--state-root",
        str(state),
        "--event-file",
        str(path),
        expected=expected,
    )


def status(state: Path, pr: int) -> dict:
    return run(
        "status",
        "--config",
        str(CONFIG),
        "--state-root",
        str(state),
        "--pr-number",
        str(pr),
    )


def queued_openclaw_request_id(state: Path, pr: int) -> str:
    current = status(state, pr)
    actions = [item for item in current["actions"] if item["kind"] == "openclaw.enqueue"]
    assert len(actions) == 1
    return actions[0]["payload"]["queue_request_id"]


def openclaw_event(
    event_id: str,
    event_type: str,
    pr: int,
    head: str,
    request_id: str,
    *,
    result: str | None = None,
    findings: int | None = None,
    reviewer: str = "openclaw-reviewer",
) -> dict:
    event = {
        "schema": "smoky.review-conductor.event.v1",
        "event_id": event_id,
        "type": event_type,
        "repository": "dinkuskit/blocks",
        "pr_number": pr,
        "base_sha": BASE,
        "head_sha": head,
        "request_id": request_id,
    }
    if result is not None:
        event.update(
            {
                "result": result,
                "finding_count": findings,
                "reviewer_actor": reviewer,
                "proof_ref": f"proof/openclaw/{event_id}/PROOF.md",
            }
        )
    return event


def adjudication_event(
    event_id: str,
    pr: int,
    head: str,
    request_id: str,
    classifications: list[str],
    *,
    reviewer: str = "openclaw-reviewer",
    rail: str = "openclaw",
    review_epoch: int | None = None,
    repair_owner: str | None = None,
    handoff: dict | None = None,
) -> dict:
    event = {
        "schema": "smoky.review-conductor.event.v1",
        "event_id": event_id,
        "type": "adjudication.completed",
        "repository": "dinkuskit/blocks",
        "pr_number": pr,
        "base_sha": BASE,
        "head_sha": head,
        "request_id": request_id,
        "rail": rail,
        "classifications": classifications,
        "reviewer_actor": reviewer,
        "proof_ref": f"proof/adjudication/{event_id}/ADJUDICATION.md",
    }
    if review_epoch is not None:
        event["review_epoch"] = review_epoch
    if repair_owner is not None:
        event["repair_owner"] = repair_owner
    if handoff is not None:
        event["mutation_handoff"] = handoff
    return event


def clawsweeper_event(
    event_id: str,
    pr: int,
    head: str,
    workflow_run_id: int,
    *,
    result: str,
    findings: int,
    reviewer: str = "saari-clawsweeper",
) -> dict:
    return {
        "schema": "smoky.review-conductor.event.v1",
        "event_id": event_id,
        "type": "clawsweeper.terminal",
        "repository": "dinkuskit/blocks",
        "pr_number": pr,
        "base_sha": BASE,
        "head_sha": head,
        "workflow_run_id": workflow_run_id,
        "result": result,
        "finding_count": findings,
        "reviewer_actor": reviewer,
        "proof_ref": f"proof/clawsweeper/{event_id}/PROOF.md",
    }


def create_fake_adapters(temp: Path) -> tuple[Path, Path, Path]:
    log = temp / "adapter-calls.jsonl"
    fake_smoky = temp / "fake-smoky"
    fake_smoky.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys, time\n"
        "time.sleep(float(os.environ.get('FAKE_ADAPTER_DELAY', '0')))\n"
        "with open(os.environ['FAKE_ADAPTER_LOG'], 'a', encoding='utf-8') as handle:\n"
        "    handle.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "kind = 'completed' if 'spark-openclaw-materialize-worktree' in sys.argv else 'queued'\n"
        "head = sys.argv[sys.argv.index('--ref') + 1] if '--ref' in sys.argv else os.environ['FAKE_HEAD']\n"
        "print(json.dumps({'result': kind, 'commit_sha': head, 'proof_path': 'fixture/PROOF.md', 'pr_url': '', 'verdict': '', 'blocker': ''}))\n",
        encoding="utf-8",
    )
    fake_smoky.chmod(0o755)
    fake_gh = temp / "fake-gh"
    fake_gh.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "with open(os.environ['FAKE_ADAPTER_LOG'], 'a', encoding='utf-8') as handle:\n"
        "    handle.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "print(json.dumps({'workflow_run_id': 4242, 'html_url': 'https://example.invalid/run/4242'}))\n",
        encoding="utf-8",
    )
    fake_gh.chmod(0o755)
    return fake_smoky, fake_gh, log


def queue_openclaw_action(temp: Path, state: Path, pr: int, head: str, *, run_id: int) -> dict:
    github_event(temp, state, "pull_request", f"delivery-{state.name}-pr-{pr}", pr_payload(pr, head))
    github_event(
        temp,
        state,
        "workflow_run",
        f"delivery-{state.name}-ci-{pr}",
        workflow_payload(pr, head, "success", run_id),
    )
    current = status(state, pr)
    actions = [item for item in current["actions"] if item["kind"] == "openclaw.enqueue"]
    assert len(actions) == 1
    return actions[0]


def expected_openclaw_commands(
    smoky: str,
    checkout: Path,
    action: dict,
    epoch: int,
    *,
    exact_tuple: bool = False,
) -> list[list[str]]:
    payload = action["payload"]
    materialize = [
        smoky,
        "lane",
        "run",
        "spark-openclaw-materialize-worktree",
        "--repo",
        str(checkout),
        "--ref",
        payload["head_sha"],
        "--base",
        payload["base_sha"],
        "--remote-worktree",
        payload["remote_worktree"],
        "--pr-url",
        payload["pr_url"],
        "--transport",
        payload["transport"],
    ]
    queue = [
        smoky,
        "lane",
        "run",
        "spark-openclaw-autoreview",
        "--queue",
        "--queue-request-id",
        payload["queue_request_id"],
        "--operator-id",
        payload["operator_id"],
        "--mode",
        "branch",
        "--base",
        payload["base_sha"],
        "--remote-worktree",
        payload["remote_worktree"],
        "--pr-url",
        payload["pr_url"],
    ]
    if exact_tuple:
        queue.extend(
            [
                "--exact-tuple-contract",
                "review-conductor-openclaw-v1",
                "--review-epoch",
                str(epoch),
            ]
        )
    return [materialize, queue]


def exact_tuple_config(temp: Path) -> Path:
    value = json.loads(CONFIG.read_text(encoding="utf-8"))
    value["openclaw"]["exact_tuple_contract"] = "review-conductor-openclaw-v1"
    return write_json(temp, "exact-tuple-blocks.json", value)


def spark_legacy_parser_script() -> str:
    lane = (ROOT / "tests" / "fixtures" / "upstream-spark-openclaw-autoreview.txt").read_text(
        encoding="utf-8"
    )
    start = lane.index("require_value() {")
    end = lane.index('\nif [ "$ACTIVATION_PREFLIGHT" = "1" ]; then')
    return "#!/usr/bin/env bash\nset -uo pipefail\n" + lane[start:end] + "exit 0\n"


def parse_spark_legacy_queue(temp: Path, queue: list[str]) -> subprocess.CompletedProcess[str]:
    script = temp / "legacy-spark-parser.sh"
    script.write_text(spark_legacy_parser_script(), encoding="utf-8")
    script.chmod(0o755)
    lane_index = queue.index("spark-openclaw-autoreview")
    return subprocess.run(
        ["bash", str(script), *queue[lane_index + 1 :]],
        cwd=temp,
        capture_output=True,
        text=True,
        timeout=10,
    )


def overwrite_action_payload(state: Path, action_id: str, **overrides: object) -> None:
    database = state / "review-conductor.sqlite3"
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT payload_json FROM actions WHERE action_id = ?", (action_id,)
        ).fetchone()
        payload = json.loads(row[0])
        payload.update(overrides)
        connection.execute(
            "UPDATE actions SET payload_json = ? WHERE action_id = ?",
            (json.dumps(payload, sort_keys=True, separators=(",", ":")), action_id),
        )


def plan_openclaw(
    state: Path,
    action_id: str,
    checkout: Path,
    *,
    smoky: Path,
    log: Path,
    head: str,
    expected: int = 0,
    apply: bool = False,
    config: Path | None = None,
) -> dict:
    args = [
        "dispatch-action",
        "--config",
        str(CONFIG if config is None else config),
        "--state-root",
        str(state),
        "--action-id",
        action_id,
        "--source-checkout",
        str(checkout),
    ]
    if apply:
        args.append("--apply")
    return run(
        *args,
        env={
            "SMOKY_REVIEW_CONDUCTOR_SMOKY": str(smoky),
            "FAKE_ADAPTER_LOG": str(log),
            "FAKE_HEAD": head,
        },
        expected=expected,
    )


def dispatch_openclaw(temp: Path, state: Path, pr: int, head: str) -> str:
    current = status(state, pr)
    actions = [item for item in current["actions"] if item["kind"] == "openclaw.enqueue"]
    assert len(actions) == 1
    fake_smoky, _, log = create_fake_adapters(temp)
    checkout = temp / f"blocks-checkout-{pr}-{head[:8]}"
    checkout.mkdir(exist_ok=True)
    dispatched = run(
        "dispatch-action",
        "--config",
        str(CONFIG),
        "--state-root",
        str(state),
        "--action-id",
        actions[0]["action_id"],
        "--source-checkout",
        str(checkout),
        "--apply",
        env={
            "SMOKY_REVIEW_CONDUCTOR_SMOKY": str(fake_smoky),
            "FAKE_ADAPTER_LOG": str(log),
            "FAKE_HEAD": head,
        },
    )
    assert dispatched["result"] == "dispatched"
    return actions[0]["payload"]["queue_request_id"]


def dispatch_clawsweeper(temp: Path, state: Path, pr: int, head: str) -> int:
    current = status(state, pr)
    actions = [item for item in current["actions"] if item["kind"] == "clawsweeper.dispatch"]
    assert len(actions) == 1
    _, fake_gh, log = create_fake_adapters(temp)
    dispatched = run(
        "dispatch-action",
        "--config",
        str(CONFIG),
        "--state-root",
        str(state),
        "--action-id",
        actions[0]["action_id"],
        "--apply",
        env={
            "SMOKY_REVIEW_CONDUCTOR_GH": str(fake_gh),
            "FAKE_ADAPTER_LOG": str(log),
            "FAKE_HEAD": head,
        },
    )
    assert dispatched["result"] == "dispatched"
    return 4242


def reach_clawsweeper_result(
    temp: Path,
    state: Path,
    pr: int,
    head: str,
    *,
    ci_run: int,
    result: str,
    findings: int,
    expected_state: str,
) -> int:
    github_event(temp, state, "pull_request", f"delivery-{state.name}-pr", pr_payload(pr, head))
    github_event(
        temp,
        state,
        "workflow_run",
        f"delivery-{state.name}-ci",
        workflow_payload(pr, head, "success", ci_run),
    )
    request_id = dispatch_openclaw(temp, state, pr, head)
    internal_event(
        temp,
        state,
        f"{state.name}-openclaw-clean",
        openclaw_event(
            f"internal:{state.name}-openclaw-clean",
            "openclaw.terminal",
            pr,
            head,
            request_id,
            result="clean",
            findings=0,
        ),
    )
    workflow_run_id = dispatch_clawsweeper(temp, state, pr, head)
    terminal = internal_event(
        temp,
        state,
        f"{state.name}-clawsweeper-{result}",
        clawsweeper_event(
            f"internal:{state.name}-clawsweeper-{result}",
            pr,
            head,
            workflow_run_id,
            result=result,
            findings=findings,
        ),
    )
    assert terminal["state"] == expected_state
    assert terminal["repair_cycle"] == 0
    current = status(state, pr)
    assert current["head"]["state"] == expected_state
    assert current["head"]["rail"] == "clawsweeper"
    assert current["head"]["review_request_id"] == str(workflow_run_id)
    assert current["head"]["review_epoch"] == 0
    assert current["head"]["reviewer_actor"] == "saari-clawsweeper"
    return workflow_run_id


def insert_clawsweeper_quality(
    state: Path,
    pr: int,
    head: str,
    workflow_run_id: int | str,
    *,
    review_epoch: int = 0,
) -> None:
    with sqlite3.connect(state / "review-conductor.sqlite3") as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS clawsweeper_quality (
              repository TEXT NOT NULL,
              pr_number INTEGER NOT NULL,
              base_sha TEXT NOT NULL,
              head_sha TEXT NOT NULL,
              review_epoch INTEGER NOT NULL,
              workflow_run_id TEXT NOT NULL,
              report_sha256 TEXT NOT NULL,
              content_verdict TEXT,
              PRIMARY KEY(repository, pr_number, base_sha, head_sha, review_epoch)
            )
            """
        )
        connection.execute(
            """
            INSERT INTO clawsweeper_quality(
              repository, pr_number, base_sha, head_sha, review_epoch,
              workflow_run_id, report_sha256, content_verdict
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "dinkuskit/blocks",
                pr,
                BASE,
                head,
                review_epoch,
                str(workflow_run_id),
                "a" * 64,
                "proof_deficient",
            ),
        )


def reach_clawsweeper_human_gate(temp: Path, state: Path, pr: int, head: str, *, ci_run: int) -> int:
    return reach_clawsweeper_result(
        temp,
        state,
        pr,
        head,
        ci_run=ci_run,
        result="human_gate",
        findings=0,
        expected_state="waiting_human",
    )


def reach_clawsweeper_findings(temp: Path, state: Path, pr: int, head: str, *, ci_run: int) -> int:
    return reach_clawsweeper_result(
        temp,
        state,
        pr,
        head,
        ci_run=ci_run,
        result="findings",
        findings=1,
        expected_state="awaiting_adjudication",
    )


def test_official_hmac_vector() -> None:
    spec = importlib.util.spec_from_file_location("review_conductor", ROOT / "tools" / "review_conductor.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.verify_github_signature(
        b"Hello, World!",
        "sha256=757107ea0eb2509fc211221cce984b8a37570b6d7586c22c46f4379c8b043e17",
        "It's a Secret to Everybody",
    )
    lane = (ROOT / "tests" / "fixtures" / "upstream-spark-openclaw-autoreview.txt").read_text(encoding="utf-8")
    assert "--queue-request-id" in lane
    assert "os.link(tmp, dest)" in lane
    assert "deterministic request id is already bound to different review identity" in lane


def test_ci_gate_dedupe_and_openclaw_dispatch(temp: Path) -> None:
    state = temp / "state-ci"
    head = "2" * 40
    github_event(temp, state, "pull_request", "delivery-pr-ci", pr_payload(101, head))
    failed = github_event(temp, state, "workflow_run", "delivery-ci-fail", workflow_payload(101, head, "failure", 1001))
    assert failed["review_invoked"] is False
    failed_status = status(state, 101)
    assert failed_status["head"]["state"] == "ci_failed"
    assert failed_status["actions"] == []

    success_payload = workflow_payload(101, head, "success", 1002)
    success = github_event(temp, state, "workflow_run", "delivery-ci-success", success_payload)
    assert success["action_created"] is True
    duplicate_delivery = github_event(temp, state, "workflow_run", "delivery-ci-success", success_payload)
    assert duplicate_delivery["result"] == "duplicate_delivery"
    second_delivery = github_event(temp, state, "workflow_run", "delivery-ci-success-retry", workflow_payload(101, head, "success", 1003))
    assert second_delivery["action_created"] is False
    current = status(state, 101)
    assert current["head"]["state"] == "openclaw_queued"
    assert [item["kind"] for item in current["actions"]] == ["openclaw.enqueue"]

    fake_smoky, _, log = create_fake_adapters(temp)
    checkout = temp / "blocks-checkout"
    checkout.mkdir()
    adapter_env = {
        "SMOKY_REVIEW_CONDUCTOR_SMOKY": str(fake_smoky),
        "FAKE_ADAPTER_LOG": str(log),
        "FAKE_HEAD": head,
    }
    action_id = current["actions"][0]["action_id"]
    dispatched = run(
        "dispatch-action", "--config", str(CONFIG), "--state-root", str(state),
        "--action-id", action_id, "--source-checkout", str(checkout), "--apply",
        env=adapter_env,
    )
    assert dispatched["result"] == "dispatched"
    again = run(
        "dispatch-action", "--config", str(CONFIG), "--state-root", str(state),
        "--action-id", action_id, "--source-checkout", str(checkout), "--apply",
        env=adapter_env,
    )
    assert again["result"] == "already_dispatched"
    calls = log.read_text(encoding="utf-8").splitlines()
    assert len(calls) == 2
    queue_call = json.loads(calls[1])
    assert "--queue-request-id" in queue_call


def test_ci_readback_reconciliation_is_exact_and_idempotent(temp: Path) -> None:
    head = "b" * 40
    state = temp / "state-readback"
    github_event(temp, state, "pull_request", "delivery-readback-pr", pr_payload(107, head))
    payload = workflow_payload(107, head, "success", 5101)
    body = write_json(temp, "readback.json", payload)
    reconciled = run(
        "reconcile-workflow-run",
        "--config", str(CONFIG),
        "--state-root", str(state),
        "--body-file", str(body),
        "--pr-number", "107",
        "--base-sha", BASE,
        "--head-sha", head,
        "--run-id", "5101",
    )
    assert reconciled["result"] == "accepted"
    assert reconciled["source"] == "github-readback"
    assert status(state, 107)["head"]["state"] == "openclaw_queued"
    duplicate = run(
        "reconcile-workflow-run",
        "--config", str(CONFIG),
        "--state-root", str(state),
        "--body-file", str(body),
        "--pr-number", "107",
        "--base-sha", BASE,
        "--head-sha", head,
        "--run-id", "5101",
    )
    assert duplicate["result"] == "duplicate_reconciliation"
    assert len(status(state, 107)["actions"]) == 1

    conflicting_head = "c" * 40
    github_event(temp, state, "pull_request", "delivery-readback-conflict-pr", pr_payload(109, conflicting_head))
    conflicting_body = write_json(
        temp,
        "readback-conflict.json",
        workflow_payload(109, conflicting_head, "success", 5101),
    )
    conflict = run(
        "reconcile-workflow-run",
        "--config", str(CONFIG),
        "--state-root", str(state),
        "--body-file", str(conflicting_body),
        "--pr-number", "109",
        "--base-sha", BASE,
        "--head-sha", conflicting_head,
        "--run-id", "5101",
        expected=2,
    )
    assert "reconciliation id was reused with different content" in conflict["stderr"]

    collision_state = temp / "state-readback-delivery-collision"
    collision_head = "f" * 40
    github_event(temp, collision_state, "pull_request", "github-reconcile-workflow-run:5401", pr_payload(112, collision_head))
    collision_body = write_json(
        temp,
        "readback-delivery-collision.json",
        workflow_payload(112, collision_head, "success", 5401),
    )
    collision = run(
        "reconcile-workflow-run",
        "--config", str(CONFIG),
        "--state-root", str(collision_state),
        "--body-file", str(collision_body),
        "--pr-number", "112",
        "--base-sha", BASE,
        "--head-sha", collision_head,
        "--run-id", "5401",
        expected=2,
    )
    assert "collided with another delivery" in collision["stderr"]

    ignored_state = temp / "state-readback-ignored-reuse"
    ignored_head = "d" * 40
    github_event(temp, ignored_state, "pull_request", "delivery-ignored-reuse-pr", pr_payload(110, ignored_head))
    ignored_delivery = github_event(
        temp,
        ignored_state,
        "workflow_run",
        "delivery-ignored-reuse-ci",
        workflow_payload(110, ignored_head, "success", 5201, base_ref="release"),
    )
    assert ignored_delivery["result"] == "ignored"
    ignored_replay_body = write_json(
        temp,
        "readback-ignored-reuse.json",
        workflow_payload(110, ignored_head, "success", 5201),
    )
    ignored_replay = run(
        "reconcile-workflow-run",
        "--config", str(CONFIG),
        "--state-root", str(ignored_state),
        "--body-file", str(ignored_replay_body),
        "--pr-number", "110",
        "--base-sha", BASE,
        "--head-sha", ignored_head,
        "--run-id", "5201",
        expected=2,
    )
    assert "previously recorded as ignored" in ignored_replay["stderr"]

    delivered_state = temp / "state-readback-already-delivered"
    github_event(temp, delivered_state, "pull_request", "delivery-original-pr", pr_payload(108, head))
    github_event(
        temp,
        delivered_state,
        "workflow_run",
        "delivery-original-ci",
        workflow_payload(108, head, "success", 5102),
    )
    delivered_body = write_json(temp, "readback-already-delivered.json", workflow_payload(108, head, "success", 5102))
    already = run(
        "reconcile-workflow-run",
        "--config", str(CONFIG),
        "--state-root", str(delivered_state),
        "--body-file", str(delivered_body),
        "--pr-number", "108",
        "--base-sha", BASE,
        "--head-sha", head,
        "--run-id", "5102",
    )
    assert already["result"] == "already_processed"
    assert len(status(delivered_state, 108)["actions"]) == 1

    late_state = temp / "state-readback-late-original"
    late_head = "e" * 40
    github_event(temp, late_state, "pull_request", "delivery-late-original-pr", pr_payload(111, late_head))
    late_body = write_json(
        temp,
        "readback-late-original.json",
        workflow_payload(111, late_head, "success", 5301),
    )
    late = run(
        "reconcile-workflow-run",
        "--config", str(CONFIG),
        "--state-root", str(late_state),
        "--body-file", str(late_body),
        "--pr-number", "111",
        "--base-sha", BASE,
        "--head-sha", late_head,
        "--run-id", "5301",
    )
    assert late["result"] == "accepted"
    late_delivery = github_event(
        temp,
        late_state,
        "workflow_run",
        "delivery-late-original-ci",
        workflow_payload(111, late_head, "success", 5301),
    )
    assert late_delivery["result"] == "duplicate"

    preadmission_state = temp / "state-readback-preadmission"
    preadmission_head = "a" * 40
    preadmission_payload = workflow_payload(113, preadmission_head, "success", 5402)
    preadmission_delivery = github_event(
        temp,
        preadmission_state,
        "workflow_run",
        "delivery-preadmission-ci",
        preadmission_payload,
    )
    assert preadmission_delivery["result"] == "stale"
    github_event(
        temp,
        preadmission_state,
        "pull_request",
        "delivery-preadmission-pr",
        pr_payload(113, preadmission_head),
    )
    preadmission_body = write_json(
        temp, "readback-preadmission.json", preadmission_payload
    )
    preadmission_replay = run(
        "reconcile-workflow-run",
        "--config", str(CONFIG),
        "--state-root", str(preadmission_state),
        "--body-file", str(preadmission_body),
        "--pr-number", "113",
        "--base-sha", BASE,
        "--head-sha", preadmission_head,
        "--run-id", "5402",
    )
    assert preadmission_replay["result"] == "accepted"
    assert len(status(late_state, 111)["actions"]) == 1

    mismatch = run(
        "reconcile-workflow-run",
        "--config", str(CONFIG),
        "--state-root", str(state),
        "--body-file", str(body),
        "--pr-number", "107",
        "--base-sha", BASE,
        "--head-sha", "c" * 40,
        "--run-id", "5101",
        expected=2,
    )
    assert "does not match the requested exact tuple" in mismatch["stderr"]

    ignored_body = write_json(
        temp,
        "readback-ignored.json",
        workflow_payload(107, head, "success", 5103, base_ref="release"),
    )
    ignored = run(
        "reconcile-workflow-run",
        "--config", str(CONFIG),
        "--state-root", str(state),
        "--body-file", str(ignored_body),
        "--pr-number", "107",
        "--base-sha", BASE,
        "--head-sha", head,
        "--run-id", "5103",
        expected=2,
    )
    assert "workflow_run read-back is ignored" in ignored["stderr"]


def test_default_branch_scope_and_atomic_dispatch_claim(temp: Path) -> None:
    out_of_scope = temp / "state-out-of-scope"
    ignored_pr = github_event(
        temp,
        out_of_scope,
        "pull_request",
        "delivery-non-main-pr",
        pr_payload(105, "9" * 40, base_ref="release"),
    )
    assert ignored_pr["result"] == "ignored"
    assert status(out_of_scope, 105)["state"] == "unknown"

    state = temp / "state-concurrent"
    head = "a" * 40
    github_event(temp, state, "pull_request", "delivery-concurrent-pr", pr_payload(106, head))
    ignored_ci = github_event(
        temp,
        state,
        "workflow_run",
        "delivery-non-main-ci",
        workflow_payload(106, head, "success", 5001, base_ref="release"),
    )
    assert ignored_ci["result"] == "ignored"
    assert status(state, 106)["head"]["state"] == "ci_running"
    github_event(
        temp,
        state,
        "workflow_run",
        "delivery-concurrent-ci",
        workflow_payload(106, head, "success", 5002),
    )
    current = status(state, 106)
    action_id = current["actions"][0]["action_id"]

    adapter_root = temp / "concurrent-adapters"
    adapter_root.mkdir()
    fake_smoky, _, log = create_fake_adapters(adapter_root)
    checkout = temp / "blocks-checkout-concurrent"
    checkout.mkdir()
    environment = os.environ.copy()
    environment.update(
        {
            "SMOKY_REVIEW_CONDUCTOR_WEBHOOK_SECRET": SECRET,
            "SMOKY_REVIEW_CONDUCTOR_SMOKY": str(fake_smoky),
            "FAKE_ADAPTER_LOG": str(log),
            "FAKE_ADAPTER_DELAY": "0.2",
            "FAKE_HEAD": head,
        }
    )
    command = [
        sys.executable,
        str(CORE),
        "dispatch-action",
        "--config",
        str(CONFIG),
        "--state-root",
        str(state),
        "--action-id",
        action_id,
        "--source-checkout",
        str(checkout),
        "--apply",
    ]
    processes = [
        subprocess.Popen(
            command,
            cwd=ROOT,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for _ in range(2)
    ]
    results = [process.communicate(timeout=30) for process in processes]
    return_codes = [process.returncode for process in processes]
    assert return_codes.count(0) == 1
    assert return_codes.count(2) == 1
    losing_output = "\n".join(stderr for _, stderr in results)
    assert (
        "another dispatcher won" in losing_output
        or "action is in uncertain dispatching state" in losing_output
    )
    assert len(log.read_text(encoding="utf-8").splitlines()) == 2
    final = status(state, 106)
    assert final["actions"][0]["status"] == "dispatched"
    assert final["actions"][0]["attempts"] == 1


def test_old_head_terminal_is_historical_only(temp: Path) -> None:
    state = temp / "state-stale"
    old_head = "3" * 40
    new_head = "4" * 40
    github_event(temp, state, "pull_request", "delivery-pr-old", pr_payload(102, old_head))
    github_event(temp, state, "workflow_run", "delivery-ci-old", workflow_payload(102, old_head, "success", 2001))
    old_request_id = queued_openclaw_request_id(state, 102)
    old_action_id = status(state, 102)["actions"][0]["action_id"]
    github_event(temp, state, "pull_request", "delivery-pr-new", pr_payload(102, new_head, "synchronize"))
    delayed_pr = github_event(
        temp,
        state,
        "pull_request",
        "delivery-pr-old-delayed",
        pr_payload(102, old_head, "synchronize"),
    )
    assert delayed_pr["result"] == "stale"
    obsolete = run(
        "dispatch-action",
        "--config",
        str(CONFIG),
        "--state-root",
        str(state),
        "--action-id",
        old_action_id,
    )
    assert obsolete["result"] == "obsolete"
    assert obsolete["external_mutation_performed"] is False
    stale = internal_event(
        temp,
        state,
        "old-openclaw-terminal",
        openclaw_event("internal:old-openclaw-terminal", "openclaw.terminal", 102, old_head, old_request_id, result="clean", findings=0),
    )
    assert stale["result"] == "stale"
    current = status(state, 102)
    assert current["head"]["head_sha"] == new_head
    assert current["head"]["state"] == "ci_running"
    assert current["actions"] == []

    unknown_old_state = temp / "state-stale-unknown"
    newest = "c" * 40
    unknown_old = "b" * 40
    github_event(
        temp,
        unknown_old_state,
        "pull_request",
        "delivery-newest-first",
        pr_payload(107, newest, updated_at="2026-08-27T00:01:02Z"),
    )
    delayed_unknown = github_event(
        temp,
        unknown_old_state,
        "pull_request",
        "delivery-unknown-old-late",
        pr_payload(
            107,
            unknown_old,
            "synchronize",
            updated_at="2026-08-27T00:01:01Z",
        ),
    )
    assert delayed_unknown["result"] == "stale"
    equal_time_conflict = github_event(
        temp,
        unknown_old_state,
        "pull_request",
        "delivery-equal-time-conflict",
        pr_payload(
            107,
            "e" * 40,
            "synchronize",
            updated_at="2026-08-27T00:01:02Z",
        ),
    )
    assert equal_time_conflict["result"] == "stale"
    unknown_current = status(unknown_old_state, 107)
    assert unknown_current["head"]["head_sha"] == newest
    assert unknown_current["head"]["state"] == "ci_running"

    revert_state = temp / "state-legitimate-revert"
    first = "e" * 40
    second = "f" * 40
    github_event(
        temp,
        revert_state,
        "pull_request",
        "delivery-revert-first",
        pr_payload(109, first, updated_at="2026-08-27T00:03:01Z"),
    )
    github_event(
        temp,
        revert_state,
        "pull_request",
        "delivery-revert-second",
        pr_payload(
            109,
            second,
            "synchronize",
            updated_at="2026-08-27T00:03:02Z",
        ),
    )
    reverted = github_event(
        temp,
        revert_state,
        "pull_request",
        "delivery-revert-back",
        pr_payload(
            109,
            first,
            "synchronize",
            updated_at="2026-08-27T00:03:03Z",
        ),
    )
    assert reverted["result"] == "accepted"
    assert status(revert_state, 109)["head"]["head_sha"] == first


def test_closed_pr_obsoletes_pending_review_and_ignores_late_ci(temp: Path) -> None:
    state = temp / "state-closed"
    head = "d" * 40
    github_event(
        temp,
        state,
        "pull_request",
        "delivery-close-open",
        pr_payload(108, head, updated_at="2026-08-27T00:02:01Z"),
    )
    github_event(
        temp,
        state,
        "workflow_run",
        "delivery-close-ci-success",
        workflow_payload(108, head, "success", 6001),
    )
    action_id = status(state, 108)["actions"][0]["action_id"]
    closed = github_event(
        temp,
        state,
        "pull_request",
        "delivery-close-terminal",
        pr_payload(
            108,
            head,
            "closed",
            updated_at="2026-08-27T00:02:02Z",
        ),
    )
    assert closed["state"] == "closed"
    current = status(state, 108)
    assert current["head"]["state"] == "closed"
    assert current["actions"][0]["status"] == "obsolete"
    obsolete = run(
        "dispatch-action",
        "--config",
        str(CONFIG),
        "--state-root",
        str(state),
        "--action-id",
        action_id,
        "--apply",
    )
    assert obsolete["result"] == "obsolete"
    assert obsolete["external_mutation_performed"] is False

    late_ci = github_event(
        temp,
        state,
        "workflow_run",
        "delivery-close-ci-late",
        workflow_payload(108, head, "success", 6002),
    )
    assert late_ci["result"] == "ignored"
    assert late_ci["review_invoked"] is False
    assert status(state, 108)["head"]["state"] == "closed"


def test_closure_during_openclaw_dispatch_blocks_queue_and_retry(temp: Path) -> None:
    state = temp / "state-close-during-dispatch"
    head = "0" * 40
    github_event(
        temp,
        state,
        "pull_request",
        "delivery-dispatch-close-open",
        pr_payload(110, head, updated_at="2026-08-27T00:04:01Z"),
    )
    github_event(
        temp,
        state,
        "workflow_run",
        "delivery-dispatch-close-ci",
        workflow_payload(110, head, "success", 7001),
    )
    action_id = status(state, 110)["actions"][0]["action_id"]
    adapter_root = temp / "close-during-dispatch-adapters"
    adapter_root.mkdir()
    fake_smoky, _, log = create_fake_adapters(adapter_root)
    checkout = temp / "blocks-checkout-close-during-dispatch"
    checkout.mkdir()
    environment = os.environ.copy()
    environment.update(
        {
            "SMOKY_REVIEW_CONDUCTOR_WEBHOOK_SECRET": SECRET,
            "SMOKY_REVIEW_CONDUCTOR_SMOKY": str(fake_smoky),
            "FAKE_ADAPTER_LOG": str(log),
            "FAKE_ADAPTER_DELAY": "0.5",
            "FAKE_HEAD": head,
        }
    )
    process = subprocess.Popen(
        [
            sys.executable,
            str(CORE),
            "dispatch-action",
            "--config",
            str(CONFIG),
            "--state-root",
            str(state),
            "--action-id",
            action_id,
            "--source-checkout",
            str(checkout),
            "--apply",
        ],
        cwd=ROOT,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    database = state / "review-conductor.sqlite3"
    for _ in range(100):
        with sqlite3.connect(database) as connection:
            claimed = connection.execute(
                "SELECT status FROM actions WHERE action_id = ?", (action_id,)
            ).fetchone()
        if claimed is not None and claimed[0] == "dispatching":
            break
        time.sleep(0.01)
    else:
        process.kill()
        raise AssertionError("OpenClaw action was not claimed for dispatch")

    closed = github_event(
        temp,
        state,
        "pull_request",
        "delivery-dispatch-close-terminal",
        pr_payload(110, head, "closed", updated_at="2026-08-27T00:04:02Z"),
    )
    assert closed["state"] == "closed"
    stdout, stderr = process.communicate(timeout=30)
    assert process.returncode == 0, f"stdout={stdout}\nstderr={stderr}"
    interrupted = json.loads(stdout)
    assert interrupted["result"] == "obsolete_during_dispatch"
    assert interrupted["external_mutation_performed"] is True
    assert interrupted["review_enqueued"] is False
    calls_before_retry = log.read_text(encoding="utf-8").splitlines()
    assert len(calls_before_retry) == 1
    assert "spark-openclaw-materialize-worktree" in calls_before_retry[0]

    retry = run(
        "dispatch-action",
        "--config",
        str(CONFIG),
        "--state-root",
        str(state),
        "--action-id",
        action_id,
        "--source-checkout",
        str(checkout),
        "--apply",
        "--retry",
        env=environment,
    )
    assert retry["result"] == "obsolete"
    assert retry["external_mutation_performed"] is False
    assert log.read_text(encoding="utf-8").splitlines() == calls_before_retry
    current = status(state, 110)
    assert current["head"]["state"] == "closed"
    assert current["actions"][0]["status"] == "obsolete"
    assert current["actions"][0]["receipt"]["result"] == "obsolete_during_dispatch"
    assert len(current["actions"][0]["receipt"]["receipts"]) == 1
    assert current["projection"]["merge_policy"] == "human_only"
    assert current["projection"]["merge_authorized"] is False


def test_same_tuple_reopen_freshness_and_action_epochs(temp: Path) -> None:
    state = temp / "state-reopen"
    head = "a" * 40
    github_event(
        temp,
        state,
        "pull_request",
        "delivery-reopen-open",
        pr_payload(111, head, updated_at="2026-08-27T00:05:01Z"),
    )
    github_event(
        temp,
        state,
        "workflow_run",
        "delivery-reopen-ci-0",
        workflow_payload(111, head, "success", 8001),
    )
    first_request_id = dispatch_openclaw(temp, state, 111, head)
    github_event(
        temp,
        state,
        "pull_request",
        "delivery-reopen-close-0",
        pr_payload(111, head, "closed", updated_at="2026-08-27T00:05:02Z"),
    )

    reopened = github_event(
        temp,
        state,
        "pull_request",
        "delivery-reopen-1",
        pr_payload(111, head, "reopened", updated_at="2026-08-27T00:05:03Z"),
    )
    assert reopened["result"] == "accepted"
    assert reopened["state"] == "ci_running"
    assert reopened["review_epoch"] == 1
    duplicate_reopen = github_event(
        temp,
        state,
        "pull_request",
        "delivery-reopen-1-duplicate",
        pr_payload(111, head, "reopened", updated_at="2026-08-27T00:05:03Z"),
    )
    assert duplicate_reopen["result"] == "duplicate"
    assert duplicate_reopen["review_epoch"] == 1
    delayed_close = github_event(
        temp,
        state,
        "pull_request",
        "delivery-reopen-delayed-close",
        pr_payload(111, head, "closed", updated_at="2026-08-27T00:05:02Z"),
    )
    assert delayed_close["result"] == "stale"
    assert status(state, 111)["head"]["state"] == "ci_running"

    old_incarnation_ci = github_event(
        temp,
        state,
        "workflow_run",
        "delivery-reopen-old-ci",
        workflow_payload(
            111,
            head,
            "success",
            8002,
            created_at="2026-08-27T00:05:02Z",
        ),
    )
    assert old_incarnation_ci["result"] == "ignored"
    assert old_incarnation_ci["review_invoked"] is False
    assert len(status(state, 111)["actions"]) == 1

    first_epoch_ci = github_event(
        temp,
        state,
        "workflow_run",
        "delivery-reopen-ci-1",
        workflow_payload(
            111,
            head,
            "success",
            8003,
            created_at="2026-08-27T00:05:04Z",
        ),
    )
    assert first_epoch_ci["action_created"] is True
    old_epoch_terminal = internal_event(
        temp,
        state,
        "reopen-old-epoch-terminal",
        openclaw_event(
            "internal:reopen-old-epoch-terminal",
            "openclaw.terminal",
            111,
            head,
            first_request_id,
            result="clean",
            findings=0,
        ),
        expected=2,
    )
    assert "does not match the exact queued action" in old_epoch_terminal["stderr"]
    assert status(state, 111)["head"]["state"] == "openclaw_queued"
    duplicate_ci = github_event(
        temp,
        state,
        "workflow_run",
        "delivery-reopen-ci-1-duplicate",
        workflow_payload(
            111,
            head,
            "success",
            8004,
            created_at="2026-08-27T00:05:04Z",
        ),
    )
    assert duplicate_ci["action_created"] is False
    after_ci = status(state, 111)
    openclaw_actions = [
        item for item in after_ci["actions"] if item["kind"] == "openclaw.enqueue"
    ]
    assert len(openclaw_actions) == 2
    assert [item["review_epoch"] for item in openclaw_actions] == [0, 1]
    assert openclaw_actions[0]["status"] == "dispatched"
    assert openclaw_actions[1]["status"] == "pending"
    assert openclaw_actions[0]["payload"]["queue_request_id"] == first_request_id
    assert (
        openclaw_actions[1]["payload"]["queue_request_id"] != first_request_id
    )

    github_event(
        temp,
        state,
        "pull_request",
        "delivery-reopen-close-1",
        pr_payload(111, head, "closed", updated_at="2026-08-27T00:05:04Z"),
    )
    duplicate_close = github_event(
        temp,
        state,
        "pull_request",
        "delivery-reopen-close-1-duplicate",
        pr_payload(111, head, "closed", updated_at="2026-08-27T00:05:04Z"),
    )
    assert duplicate_close["result"] == "duplicate"
    stale_reopen = github_event(
        temp,
        state,
        "pull_request",
        "delivery-reopen-old-after-close",
        pr_payload(111, head, "reopened", updated_at="2026-08-27T00:05:03Z"),
    )
    assert stale_reopen["result"] == "stale"
    assert status(state, 111)["head"]["state"] == "closed"
    reopened_again = github_event(
        temp,
        state,
        "pull_request",
        "delivery-reopen-2",
        pr_payload(111, head, "reopened", updated_at="2026-08-27T00:05:05Z"),
    )
    assert reopened_again["state"] == "ci_running"
    assert reopened_again["review_epoch"] == 2
    final = status(state, 111)
    assert final["projection"]["ready_for_human_label"] is False
    assert final["projection"]["merge_policy"] == "human_only"
    assert final["projection"]["merge_authorized"] is False
    assert all(item["kind"] != "merge" for item in final["actions"])


def test_legacy_database_migrates_review_epoch(temp: Path) -> None:
    state = temp / "state-legacy-migration"
    state.mkdir(mode=0o700)
    database = state / "review-conductor.sqlite3"
    head = "b" * 40
    payload = {
        "schema": "smoky.review-conductor.action.v1",
        "kind": "openclaw.enqueue",
        "repository": "dinkuskit/blocks",
        "pr_number": 112,
        "base_sha": BASE,
        "head_sha": head,
        "pr_url": "https://github.com/dinkuskit/blocks/pull/112",
        "operator_id": "review-conductor",
        "queue_request_id": "rc-legacy",
        "remote_worktree": "/tmp/legacy-review",
        "merge_authorized": False,
    }
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE heads (
              repository TEXT NOT NULL, pr_number INTEGER NOT NULL,
              base_sha TEXT NOT NULL, head_sha TEXT NOT NULL,
              source_updated_at TEXT NOT NULL, is_current INTEGER NOT NULL,
              state TEXT NOT NULL, author TEXT NOT NULL,
              mutation_owner TEXT NOT NULL, repair_cycle INTEGER NOT NULL,
              ci_conclusion TEXT, rail TEXT, review_request_id TEXT,
              reviewer_actor TEXT, repair_owner TEXT, blocker TEXT,
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
              PRIMARY KEY (repository, pr_number, base_sha, head_sha)
            );
            CREATE TABLE actions (
              action_id TEXT PRIMARY KEY, idempotency_key TEXT NOT NULL UNIQUE,
              kind TEXT NOT NULL, repository TEXT NOT NULL, pr_number INTEGER NOT NULL,
              base_sha TEXT NOT NULL, head_sha TEXT NOT NULL, status TEXT NOT NULL,
              payload_json TEXT NOT NULL, receipt_json TEXT, attempts INTEGER NOT NULL DEFAULT 0,
              last_error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            """
        )
        connection.execute(
            """
            INSERT INTO heads VALUES (?, ?, ?, ?, ?, 1, 'openclaw_queued', 'alice',
              'alice', 0, NULL, NULL, NULL, NULL, NULL, NULL, ?, ?)
            """,
            (
                "dinkuskit/blocks",
                112,
                BASE,
                head,
                "2026-08-27T00:06:01.000000Z",
                "2026-08-27T00:06:01Z",
                "2026-08-27T00:06:01Z",
            ),
        )
        connection.execute(
            """
            INSERT INTO actions VALUES (
              'act-legacy', 'sha256:legacy', 'openclaw.enqueue', ?, ?, ?, ?,
              'pending', ?, NULL, 0, NULL, ?, ?
            )
            """,
            (
                "dinkuskit/blocks",
                112,
                BASE,
                head,
                json.dumps(payload, sort_keys=True, separators=(",", ":")),
                "2026-08-27T00:06:01Z",
                "2026-08-27T00:06:01Z",
            ),
        )
    migrated = status(state, 112)
    assert migrated["head"]["review_epoch"] == 0
    assert migrated["actions"][0]["review_epoch"] == 0
    assert migrated["actions"][0]["payload"]["review_epoch"] == 0


def test_repair_owner_and_two_cycle_governor(temp: Path) -> None:
    state = temp / "state-repair"
    heads = ["5" * 40, "6" * 40, "7" * 40, "c" * 40]
    for index, head in enumerate(heads):
        action = "opened" if index == 0 else "synchronize"
        github_event(temp, state, "pull_request", f"delivery-repair-pr-{index}", pr_payload(103, head, action))
        github_event(temp, state, "workflow_run", f"delivery-repair-ci-{index}", workflow_payload(103, head, "success", 3000 + index))
        request_id = dispatch_openclaw(temp, state, 103, head)
        internal_event(
            temp,
            state,
            f"repair-findings-{index}",
            openclaw_event(f"internal:repair-findings-{index}", "openclaw.terminal", 103, head, request_id, result="findings", findings=1),
        )
        current = status(state, 103)
        if index == 3:
            assert current["head"]["state"] == "awaiting_adjudication"
            assert current["head"]["repair_cycle"] == 2
            assert not [
                item
                for item in current["actions"]
                if item["kind"] == "repair.route" and item["head_sha"] == head
            ]
            continue
        if index == 0:
            rejected = internal_event(
                temp,
                state,
                "reviewer-self-repair",
                adjudication_event("internal:reviewer-self-repair", 103, head, request_id, ["required_fix"], repair_owner="openclaw-reviewer"),
                expected=2,
            )
            assert "reviewer may not own repair" in rejected["stderr"]
        adjudicated = internal_event(
            temp,
            state,
            f"repair-adjudication-{index}",
            adjudication_event(f"internal:repair-adjudication-{index}", 103, head, request_id, ["required_fix"]),
        )
        current = status(state, 103)
        assert adjudicated["state"] == "repair_required"
        repair_actions = [item for item in current["actions"] if item["kind"] == "repair.route"]
        assert len(repair_actions) == 1
        assert repair_actions[0]["payload"]["repair_owner"] == "alice"
        assert repair_actions[0]["payload"]["mutation_owner_count"] == 1
        if index >= 2:
            assert current["head"]["repair_cycle"] == 2
            assert repair_actions[0]["payload"]["repair_cycle"] == 2


def test_openclaw_clean_dispatches_clawsweeper_and_never_merge(temp: Path) -> None:
    state = temp / "state-clean"
    head = "8" * 40
    github_event(temp, state, "pull_request", "delivery-clean-pr", pr_payload(104, head))
    github_event(temp, state, "workflow_run", "delivery-clean-ci", workflow_payload(104, head, "success", 4001))
    request_id = dispatch_openclaw(temp, state, 104, head)
    internal_event(
        temp,
        state,
        "clean-openclaw-start",
        openclaw_event("internal:clean-openclaw-start", "openclaw.started", 104, head, request_id),
    )
    clean = internal_event(
        temp,
        state,
        "clean-openclaw-terminal",
        openclaw_event("internal:clean-openclaw-terminal", "openclaw.terminal", 104, head, request_id, result="clean", findings=0),
    )
    assert clean["state"] == "clawsweeper_queued"
    current = status(state, 104)
    claw_actions = [item for item in current["actions"] if item["kind"] == "clawsweeper.dispatch"]
    assert len(claw_actions) == 1
    assert claw_actions[0]["payload"]["base_sha"] == BASE
    assert claw_actions[0]["payload"]["head_sha"] == head
    assert claw_actions[0]["payload"]["workflow_id"] == "clawsweeper-native-canary.yml"

    _, fake_gh, log = create_fake_adapters(temp)
    adapter_env = {
        "SMOKY_REVIEW_CONDUCTOR_GH": str(fake_gh),
        "FAKE_ADAPTER_LOG": str(log),
        "FAKE_HEAD": head,
    }
    action_id = claw_actions[0]["action_id"]
    dispatch = run(
        "dispatch-action", "--config", str(CONFIG), "--state-root", str(state),
        "--action-id", action_id, "--apply", env=adapter_env,
    )
    assert dispatch["result"] == "dispatched"
    duplicate = run(
        "dispatch-action", "--config", str(CONFIG), "--state-root", str(state),
        "--action-id", action_id, "--apply", env=adapter_env,
    )
    assert duplicate["result"] == "already_dispatched"
    call = json.loads(log.read_text(encoding="utf-8").splitlines()[-1])
    assert f"inputs[pr_number]=104" in call
    assert f"inputs[expected_base_sha]={BASE}" in call
    assert f"inputs[expected_head_sha]={head}" in call

    terminal = internal_event(
        temp,
        state,
        "clean-clawsweeper-terminal",
        {
            "schema": "smoky.review-conductor.event.v1",
            "event_id": "internal:clean-clawsweeper-terminal",
            "type": "clawsweeper.terminal",
            "repository": "dinkuskit/blocks",
            "pr_number": 104,
            "base_sha": BASE,
            "head_sha": head,
            "workflow_run_id": 4242,
            "result": "clean",
            "finding_count": 0,
            "reviewer_actor": "saari-clawsweeper",
            "proof_ref": "proof/clawsweeper/run-4242/PROOF.md",
        },
    )
    assert terminal["state"] == "ready_for_human_merge"
    final = status(state, 104)
    assert final["projection"]["ready_for_human_label"] is True
    assert final["projection"]["merge_policy"] == "human_only"
    assert final["projection"]["merge_authorized"] is False
    assert terminal["merge_dispatched"] is False
    assert all(item["kind"] != "merge" for item in final["actions"])


def test_legacy_openclaw_queue_omits_exact_tuple_flags(temp: Path) -> None:
    state = temp / "state-legacy-queue"
    head = "c" * 40
    action = queue_openclaw_action(temp, state, 203, head, run_id=9203)
    adapter_root = temp / "legacy-queue-adapters"
    adapter_root.mkdir()
    fake_smoky, _, log = create_fake_adapters(adapter_root)
    checkout = temp / "legacy-queue-checkout"
    checkout.mkdir()
    planned = plan_openclaw(
        state, action["action_id"], checkout, smoky=fake_smoky, log=log, head=head
    )
    assert planned["result"] == "planned"
    expected = expected_openclaw_commands(str(fake_smoky), checkout, action, 0)
    assert planned["commands"] == expected
    materialize, queue = planned["commands"]
    assert "--exact-tuple-contract" not in materialize
    assert "--review-epoch" not in materialize
    assert "--exact-tuple-contract" not in queue
    assert "--review-epoch" not in queue
    parsed = parse_spark_legacy_queue(temp, queue)
    assert parsed.returncode == 0, parsed.stderr
    ungated = list(queue) + ["--exact-tuple-contract", "review-conductor-openclaw-v1", "--review-epoch", "0"]
    rejected = parse_spark_legacy_queue(temp, ungated)
    assert rejected.returncode == 2
    assert "unknown arg: --exact-tuple-contract" in rejected.stderr


def test_openclaw_queue_command_binds_exact_tuple_contract(temp: Path) -> None:
    state = temp / "state-exact-contract"
    head = "c" * 40
    action = queue_openclaw_action(temp, state, 201, head, run_id=9201)
    assert action["review_epoch"] == 0
    assert action["payload"]["review_epoch"] == 0
    adapter_root = temp / "exact-contract-adapters"
    adapter_root.mkdir()
    fake_smoky, _, log = create_fake_adapters(adapter_root)
    checkout = temp / "exact-contract-checkout"
    checkout.mkdir()
    config = exact_tuple_config(temp)
    planned = plan_openclaw(
        state,
        action["action_id"],
        checkout,
        smoky=fake_smoky,
        log=log,
        head=head,
        config=config,
    )
    assert planned["result"] == "planned"
    expected = expected_openclaw_commands(
        str(fake_smoky), checkout, action, 0, exact_tuple=True
    )
    assert planned["commands"] == expected
    materialize, queue = planned["commands"]
    assert materialize == expected[0]
    assert queue == expected[1]
    assert queue.count("--exact-tuple-contract") == 1
    assert queue.count("review-conductor-openclaw-v1") == 1
    assert queue.count("--review-epoch") == 1
    assert queue.count("0") == 1
    assert "--exact-tuple-contract" not in materialize
    assert "--review-epoch" not in materialize
    assert "review_scope" not in queue
    assert "reviewer_actor" not in queue
    assert "native_max_priority" not in queue
    assert "comprehensive" not in queue
    assert "spark-openclaw" not in queue
    assert "P3" not in queue
    assert not log.exists()

    dispatched = plan_openclaw(
        state,
        action["action_id"],
        checkout,
        smoky=fake_smoky,
        log=log,
        head=head,
        apply=True,
        config=config,
    )
    assert dispatched["result"] == "dispatched"
    again = plan_openclaw(
        state,
        action["action_id"],
        checkout,
        smoky=fake_smoky,
        log=log,
        head=head,
        apply=True,
        config=config,
    )
    assert again["result"] == "already_dispatched"
    calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert len(calls) == 2
    assert calls[0] == materialize[1:]
    assert calls[1] == queue[1:]

    github_event(
        temp,
        state,
        "pull_request",
        "delivery-exact-contract-close",
        pr_payload(201, head, "closed", updated_at="2026-08-27T00:07:02Z"),
    )
    github_event(
        temp,
        state,
        "pull_request",
        "delivery-exact-contract-reopen",
        pr_payload(201, head, "reopened", updated_at="2026-08-27T00:07:03Z"),
    )
    github_event(
        temp,
        state,
        "workflow_run",
        "delivery-exact-contract-ci-1",
        workflow_payload(201, head, "success", 9202, created_at="2026-08-27T00:07:04Z"),
    )
    reopened = status(state, 201)
    openclaw_actions = [
        item for item in reopened["actions"] if item["kind"] == "openclaw.enqueue"
    ]
    assert [item["review_epoch"] for item in openclaw_actions] == [0, 1]
    pending = openclaw_actions[1]
    assert pending["status"] == "pending"
    assert pending["payload"]["review_epoch"] == 1
    reopened_checkout = temp / "exact-contract-checkout-epoch-1"
    reopened_checkout.mkdir()
    reopened_planned = plan_openclaw(
        state,
        pending["action_id"],
        reopened_checkout,
        smoky=fake_smoky,
        log=log,
        head=head,
        config=config,
    )
    assert reopened_planned["result"] == "planned"
    reopened_expected = expected_openclaw_commands(
        str(fake_smoky), reopened_checkout, pending, 1, exact_tuple=True
    )
    assert reopened_planned["commands"] == reopened_expected
    reopened_queue = reopened_planned["commands"][1]
    assert reopened_queue.count("--exact-tuple-contract") == 1
    assert reopened_queue.count("review-conductor-openclaw-v1") == 1
    assert reopened_queue.count("--review-epoch") == 1
    assert reopened_queue[-2:] == ["--review-epoch", "1"]
    assert log.read_text(encoding="utf-8").count("\n") == 2


def test_openclaw_queue_rejects_unbound_payload_review_epoch(temp: Path) -> None:
    cases = (
        ("negative", -1),
        ("bool", True),
        ("string", "0"),
        ("mismatched", 1),
    )
    for label, value in cases:
        state = temp / f"state-unbound-epoch-{label}"
        head = "d" * 40
        action = queue_openclaw_action(temp, state, 202, head, run_id=9302)
        overwrite_action_payload(state, action["action_id"], review_epoch=value)
        adapter_root = temp / f"unbound-epoch-{label}"
        adapter_root.mkdir()
        fake_smoky, _, log = create_fake_adapters(adapter_root)
        checkout = temp / f"unbound-epoch-checkout-{label}"
        checkout.mkdir()
        planned = plan_openclaw(
            state,
            action["action_id"],
            checkout,
            smoky=fake_smoky,
            log=log,
            head=head,
            expected=2,
        )
        assert "review_epoch" in planned["stderr"]
        assert not log.exists()
        applied = plan_openclaw(
            state,
            action["action_id"],
            checkout,
            smoky=fake_smoky,
            log=log,
            head=head,
            expected=2,
            apply=True,
        )
        assert "review_epoch" in applied["stderr"]
        assert not log.exists()
        current = status(state, 202)
        assert current["actions"][0]["status"] == "pending"


def test_clawsweeper_human_gate_owner_adjudication_reaches_ready_without_rerun(temp: Path) -> None:
    state = temp / "state-claw-proof-gap"
    head = "a" * 40
    workflow_run_id = reach_clawsweeper_human_gate(temp, state, 301, head, ci_run=9301)
    insert_clawsweeper_quality(state, 301, head, workflow_run_id)
    before = status(state, 301)
    adjudicated = internal_event(
        temp,
        state,
        "claw-proof-gap-adjudication",
        adjudication_event(
            "internal:claw-proof-gap-adjudication",
            301,
            head,
            str(workflow_run_id),
            ["defer", "reject_false_positive"],
            reviewer="saari-clawsweeper",
            rail="clawsweeper",
            review_epoch=0,
        ),
    )
    assert adjudicated["state"] == "ready_for_human_merge"
    assert adjudicated["merge_dispatched"] is False
    assert adjudicated["action_created"] is False
    assert adjudicated["repair_cycle"] == 0
    after = status(state, 301)
    assert after["head"]["state"] == "ready_for_human_merge"
    assert after["head"]["rail"] == "clawsweeper"
    assert after["head"]["review_request_id"] == str(workflow_run_id)
    assert after["head"]["review_epoch"] == before["head"]["review_epoch"] == 0
    assert after["head"]["reviewer_actor"] == "saari-clawsweeper"
    assert after["head"]["base_sha"] == BASE
    assert after["head"]["head_sha"] == head
    assert after["projection"]["ready_for_human_label"] is True
    assert after["projection"]["merge_policy"] == "human_only"
    assert after["projection"]["merge_authorized"] is False
    assert after["projection"]["checks"]["ClawSweeper Review Rail"] == "success"
    assert [item["kind"] for item in after["actions"] if item["kind"] == "clawsweeper.dispatch"] == [
        item["kind"] for item in before["actions"] if item["kind"] == "clawsweeper.dispatch"
    ]
    assert len([item for item in after["actions"] if item["kind"] == "clawsweeper.dispatch"]) == 1
    assert all(item["kind"] != "repair.route" for item in after["actions"])
    assert all(item["kind"] != "merge" for item in after["actions"])


def test_clawsweeper_waiting_human_adjudication_rejects_stale_and_unsupported(temp: Path) -> None:
    state = temp / "state-claw-proof-gap-negatives"
    head = "b" * 40
    workflow_run_id = reach_clawsweeper_human_gate(temp, state, 302, head, ci_run=9302)
    request_id = str(workflow_run_id)
    valid = {
        "reviewer": "saari-clawsweeper",
        "rail": "clawsweeper",
        "review_epoch": 0,
    }
    stale_tuple = internal_event(
        temp,
        state,
        "stale-tuple",
        adjudication_event(
            "internal:stale-tuple",
            302,
            "c" * 40,
            request_id,
            ["defer"],
            **valid,
        ),
    )
    assert stale_tuple["result"] == "stale"
    cases = (
        (
            "stale-epoch",
            adjudication_event("internal:stale-epoch", 302, head, request_id, ["defer"], **{**valid, "review_epoch": 1}),
            "review epoch is stale",
        ),
        (
            "mismatched-request",
            adjudication_event("internal:mismatched-request", 302, head, "9999", ["defer"], **valid),
            "does not match the exact rail request",
        ),
        (
            "mismatched-rail",
            adjudication_event("internal:mismatched-rail", 302, head, request_id, ["defer"], **{**valid, "rail": "openclaw"}),
            "does not match the exact rail request",
        ),
        (
            "mismatched-actor",
            adjudication_event("internal:mismatched-actor", 302, head, request_id, ["defer"], **{**valid, "reviewer": "spark-openclaw"}),
            "authoritative repository reviewer",
        ),
        (
            "required-fix",
            adjudication_event("internal:required-fix", 302, head, request_id, ["required_fix"], **valid),
            "supports only defer and reject_false_positive",
        ),
        (
            "human-gate-class",
            adjudication_event("internal:human-gate-class", 302, head, request_id, ["human_gate"], **valid),
            "supports only defer and reject_false_positive",
        ),
        (
            "mixed-required-fix",
            adjudication_event(
                "internal:mixed-required-fix",
                302,
                head,
                request_id,
                ["defer", "required_fix"],
                **valid,
            ),
            "supports only defer and reject_false_positive",
        ),
    )
    for name, payload, needle in cases:
        rejected = internal_event(temp, state, name, payload, expected=2)
        assert needle in rejected["stderr"], name
    current = status(state, 302)
    assert current["head"]["state"] == "waiting_human"
    assert current["head"]["review_epoch"] == 0
    assert current["head"]["review_request_id"] == request_id
    assert current["head"]["rail"] == "clawsweeper"
    assert len([item for item in current["actions"] if item["kind"] == "clawsweeper.dispatch"]) == 1
    assert all(item["kind"] != "repair.route" for item in current["actions"])


def test_clawsweeper_waiting_human_adjudication_requires_human_gate_terminal(temp: Path) -> None:
    findings_state = temp / "state-claw-findings-human-gate"
    findings_head = "e" * 40
    findings_run = reach_clawsweeper_findings(temp, findings_state, 304, findings_head, ci_run=9304)
    findings_request = str(findings_run)
    classified = internal_event(
        temp,
        findings_state,
        "findings-human-gate",
        adjudication_event(
            "internal:findings-human-gate",
            304,
            findings_head,
            findings_request,
            ["human_gate"],
            reviewer="saari-clawsweeper",
            rail="clawsweeper",
            review_epoch=0,
        ),
    )
    assert classified["state"] == "waiting_human"
    rejected_findings = internal_event(
        temp,
        findings_state,
        "findings-human-gate-defer",
        adjudication_event(
            "internal:findings-human-gate-defer",
            304,
            findings_head,
            findings_request,
            ["defer"],
            reviewer="saari-clawsweeper",
            rail="clawsweeper",
            review_epoch=0,
        ),
        expected=2,
    )
    assert "requires the exact human_gate terminal" in rejected_findings["stderr"]
    findings_current = status(findings_state, 304)
    assert findings_current["head"]["state"] == "waiting_human"
    assert findings_current["head"]["rail"] == "clawsweeper"
    assert findings_current["head"]["review_request_id"] == findings_request
    assert findings_current["head"]["review_epoch"] == 0
    assert all(item["kind"] != "repair.route" for item in findings_current["actions"])
    assert all(item["kind"] != "merge" for item in findings_current["actions"])

    exhausted_state = temp / "state-claw-exhausted-repair"
    exhausted_head = "f" * 40
    exhausted_run = reach_clawsweeper_findings(temp, exhausted_state, 305, exhausted_head, ci_run=9305)
    exhausted_request = str(exhausted_run)
    with sqlite3.connect(exhausted_state / "review-conductor.sqlite3") as connection:
        updated = connection.execute(
            "UPDATE heads SET repair_cycle = 2 WHERE pr_number = ? AND is_current = 1",
            (305,),
        )
        assert updated.rowcount == 1
    exhausted = internal_event(
        temp,
        exhausted_state,
        "exhausted-required-fix",
        adjudication_event(
            "internal:exhausted-required-fix",
            305,
            exhausted_head,
            exhausted_request,
            ["required_fix"],
            reviewer="saari-clawsweeper",
            rail="clawsweeper",
            review_epoch=0,
        ),
    )
    assert exhausted["state"] == "repair_required"
    exhausted_current = status(exhausted_state, 305)
    assert exhausted_current["head"]["state"] == "repair_required"
    assert exhausted_current["head"]["repair_cycle"] == 2
    assert exhausted_current["head"]["rail"] == "clawsweeper"
    assert exhausted_current["head"]["review_request_id"] == exhausted_request
    scoped = [item for item in exhausted_current["actions"] if item["kind"] == "repair.route"]
    assert len(scoped) == 1
    assert scoped[0]["payload"]["repair_cycle"] == 2
    assert scoped[0]["payload"]["merge_authorized"] is False
    assert all(item["kind"] != "merge" for item in exhausted_current["actions"])


def test_clawsweeper_waiting_human_adjudication_rejects_mismatched_quality_workflow_run(
    temp: Path,
) -> None:
    state = temp / "state-claw-mismatched-quality"
    head = "9" * 40
    workflow_run_id = reach_clawsweeper_human_gate(temp, state, 306, head, ci_run=9306)
    insert_clawsweeper_quality(state, 306, head, int(workflow_run_id) + 1)
    rejected = internal_event(
        temp,
        state,
        "mismatched-quality-workflow-run",
        adjudication_event(
            "internal:mismatched-quality-workflow-run",
            306,
            head,
            str(workflow_run_id),
            ["defer", "reject_false_positive"],
            reviewer="saari-clawsweeper",
            rail="clawsweeper",
            review_epoch=0,
        ),
        expected=2,
    )
    assert "workflow_run_id does not match the exact request" in rejected["stderr"]
    current = status(state, 306)
    assert current["head"]["state"] == "waiting_human"
    assert current["head"]["rail"] == "clawsweeper"
    assert current["head"]["review_request_id"] == str(workflow_run_id)
    assert current["head"]["review_epoch"] == 0
    assert current["head"]["reviewer_actor"] == "saari-clawsweeper"
    assert current["projection"]["merge_authorized"] is False
    assert len([item for item in current["actions"] if item["kind"] == "clawsweeper.dispatch"]) == 1
    assert all(item["kind"] != "repair.route" for item in current["actions"])
    assert all(item["kind"] != "merge" for item in current["actions"])


def test_openclaw_human_gate_adjudication_remains_fail_closed(temp: Path) -> None:
    state = temp / "state-openclaw-human-gate"
    head = "d" * 40
    github_event(temp, state, "pull_request", "delivery-openclaw-gate-pr", pr_payload(303, head))
    github_event(
        temp,
        state,
        "workflow_run",
        "delivery-openclaw-gate-ci",
        workflow_payload(303, head, "success", 9303),
    )
    request_id = dispatch_openclaw(temp, state, 303, head)
    terminal = internal_event(
        temp,
        state,
        "openclaw-human-gate",
        openclaw_event(
            "internal:openclaw-human-gate",
            "openclaw.terminal",
            303,
            head,
            request_id,
            result="human_gate",
            findings=0,
        ),
    )
    assert terminal["state"] == "waiting_human"
    rejected = internal_event(
        temp,
        state,
        "openclaw-human-gate-adjudication",
        adjudication_event(
            "internal:openclaw-human-gate-adjudication",
            303,
            head,
            request_id,
            ["defer"],
            reviewer="openclaw-reviewer",
            rail="openclaw",
            review_epoch=0,
        ),
        expected=2,
    )
    assert "adjudication is invalid from state waiting_human" in rejected["stderr"]
    current = status(state, 303)
    assert current["head"]["state"] == "waiting_human"
    assert current["head"]["rail"] == "openclaw"
    assert current["head"]["review_request_id"] == request_id
    assert current["head"]["review_epoch"] == 0
    assert all(item["kind"] != "clawsweeper.dispatch" for item in current["actions"])


def test_precise_openclaw_exact_contract_mutants() -> None:
    source = (ROOT / "tools" / "review_conductor.py").read_text(encoding="utf-8")
    mutants = (
        (
            "omit exact-tuple-contract flag",
            '                "--exact-tuple-contract", OPENCLAW_EXACT_TUPLE_CONTRACT,\n                "--review-epoch", str(review_epoch),\n',
            '                "--review-epoch", str(review_epoch),\n',
            "test_openclaw_queue_command_binds_exact_tuple_contract",
        ),
        (
            "omit review-epoch flag",
            '                "--exact-tuple-contract", OPENCLAW_EXACT_TUPLE_CONTRACT,\n                "--review-epoch", str(review_epoch),\n',
            '                "--exact-tuple-contract", OPENCLAW_EXACT_TUPLE_CONTRACT,\n',
            "test_openclaw_queue_command_binds_exact_tuple_contract",
        ),
        (
            "source epoch only from payload",
            "        review_epoch = bound_openclaw_queue_epoch(action, payload)\n",
            '        review_epoch = payload["review_epoch"]\n',
            "test_openclaw_queue_rejects_unbound_payload_review_epoch",
        ),
        (
            "adjudicate OpenClaw waiting_human",
            '            if row["rail"] != "clawsweeper":\n',
            "            if False and row[\"rail\"] != \"clawsweeper\":\n",
            "test_openclaw_human_gate_adjudication_remains_fail_closed",
        ),
        (
            "allow required_fix from clawsweeper waiting_human",
            '            if not classifications <= {"defer", "reject_false_positive"}:\n',
            '            if not classifications <= {"defer", "reject_false_positive", "required_fix"}:\n',
            "test_clawsweeper_waiting_human_adjudication_rejects_stale_and_unsupported",
        ),
        (
            "accept waiting_human without human_gate terminal",
            '            if terminal is None or terminal.get("result") != "human_gate":\n',
            '            if False and (terminal is None or terminal.get("result") != "human_gate"):\n',
            "test_clawsweeper_waiting_human_adjudication_requires_human_gate_terminal",
        ),
        (
            "accept mismatched clawsweeper quality workflow_run_id",
            "    if str(quality_workflow_run_id) != workflow_run_id:\n",
            "    if False and str(quality_workflow_run_id) != workflow_run_id:\n",
            "test_clawsweeper_waiting_human_adjudication_rejects_mismatched_quality_workflow_run",
        ),
        (
            "restore required_fix ceiling at cycle 2",
            '            current_cycle = int(row["repair_cycle"])\n            ledger_limit = int(config["max_repair_cycles"])\n',
            '            if int(row["repair_cycle"]) >= int(config["max_repair_cycles"]):\n                raise ContractError("two automatic repair cycles exhausted")\n            current_cycle = int(row["repair_cycle"])\n            ledger_limit = int(config["max_repair_cycles"])\n',
            "test_repair_owner_and_two_cycle_governor",
        ),
        (
            "increment ledger past two on later repair heads",
            "            if repair_cycle < 2:\n                repair_cycle += 1\n",
            "            repair_cycle += 1\n",
            "test_repair_owner_and_two_cycle_governor",
        ),
    )
    for label, old, new, test_name in mutants:
        assert source.count(old) == 1, f"mutant anchor drifted: {label}"
        with tempfile.TemporaryDirectory(prefix="review-conductor-mutant-") as temp_name:
            copy_root = Path(temp_name) / "copy"
            for name in ("tools", "tests", "contracts"):
                shutil.copytree(ROOT / name, copy_root / name)
            target = copy_root / "tools" / "review_conductor.py"
            target.write_text(source.replace(old, new, 1), encoding="utf-8")
            completed = subprocess.run(
                [sys.executable, str(copy_root / "tests" / "test_review_conductor.py"), test_name],
                cwd=copy_root,
                capture_output=True,
                text=True,
                timeout=60,
                env={
                    "PATH": "/usr/bin:/bin",
                    "HOME": temp_name,
                    "PYTHONDONTWRITEBYTECODE": "1",
                },
            )
        assert completed.returncode != 0, f"mutant survived: {label}\n{completed.stderr}"
        assert "AssertionError" in completed.stderr or "assert " in completed.stderr, (
            f"mutant did not fail its intended assertion: {label}\n{completed.stderr}"
        )


def main() -> int:
    selected = {argument for argument in sys.argv[1:] if not argument.startswith("-")}
    named = {
        "test_official_hmac_vector": lambda _temp: test_official_hmac_vector(),
        "test_ci_gate_dedupe_and_openclaw_dispatch": test_ci_gate_dedupe_and_openclaw_dispatch,
        "test_ci_readback_reconciliation_is_exact_and_idempotent": test_ci_readback_reconciliation_is_exact_and_idempotent,
        "test_default_branch_scope_and_atomic_dispatch_claim": test_default_branch_scope_and_atomic_dispatch_claim,
        "test_old_head_terminal_is_historical_only": test_old_head_terminal_is_historical_only,
        "test_closed_pr_obsoletes_pending_review_and_ignores_late_ci": test_closed_pr_obsoletes_pending_review_and_ignores_late_ci,
        "test_closure_during_openclaw_dispatch_blocks_queue_and_retry": test_closure_during_openclaw_dispatch_blocks_queue_and_retry,
        "test_same_tuple_reopen_freshness_and_action_epochs": test_same_tuple_reopen_freshness_and_action_epochs,
        "test_legacy_database_migrates_review_epoch": test_legacy_database_migrates_review_epoch,
        "test_repair_owner_and_two_cycle_governor": test_repair_owner_and_two_cycle_governor,
        "test_openclaw_clean_dispatches_clawsweeper_and_never_merge": test_openclaw_clean_dispatches_clawsweeper_and_never_merge,
        "test_legacy_openclaw_queue_omits_exact_tuple_flags": test_legacy_openclaw_queue_omits_exact_tuple_flags,
        "test_openclaw_queue_command_binds_exact_tuple_contract": test_openclaw_queue_command_binds_exact_tuple_contract,
        "test_openclaw_queue_rejects_unbound_payload_review_epoch": test_openclaw_queue_rejects_unbound_payload_review_epoch,
        "test_clawsweeper_human_gate_owner_adjudication_reaches_ready_without_rerun": test_clawsweeper_human_gate_owner_adjudication_reaches_ready_without_rerun,
        "test_clawsweeper_waiting_human_adjudication_rejects_stale_and_unsupported": test_clawsweeper_waiting_human_adjudication_rejects_stale_and_unsupported,
        "test_clawsweeper_waiting_human_adjudication_requires_human_gate_terminal": test_clawsweeper_waiting_human_adjudication_requires_human_gate_terminal,
        "test_clawsweeper_waiting_human_adjudication_rejects_mismatched_quality_workflow_run": test_clawsweeper_waiting_human_adjudication_rejects_mismatched_quality_workflow_run,
        "test_openclaw_human_gate_adjudication_remains_fail_closed": test_openclaw_human_gate_adjudication_remains_fail_closed,
        "test_precise_openclaw_exact_contract_mutants": lambda _temp: test_precise_openclaw_exact_contract_mutants(),
    }
    if selected - set(named):
        raise SystemExit(f"unknown review conductor tests: {sorted(selected - set(named))}")
    names = [name for name in named if not selected or name in selected]
    with tempfile.TemporaryDirectory(prefix="review-conductor-test-") as temp_name:
        temp = Path(temp_name)
        for name in names:
            named[name](temp)
    print("review conductor integration tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
