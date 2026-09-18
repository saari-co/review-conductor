#!/usr/bin/env python3
"""Regression proof for maintainer-triggered ClawSweeper rereview."""

from __future__ import annotations

import json
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
import test_review_conductor as legacy  # noqa: E402
import test_review_conductor_userland as userland_tests  # noqa: E402


def comment_payload(
    pr: int,
    body: str,
    *,
    comment_id: int = 9001,
    login: str = "maintainer",
    user_type: str = "User",
    association: str = "OWNER",
    action: str = "created",
    created_at: str = "2026-08-27T01:00:00Z",
    issue_body: str = "fresh packaging proof",
    pull_request: bool = True,
    repository: str = "dinkuskit/blocks",
    repository_id: int = 1306882611,
) -> dict[str, Any]:
    issue: dict[str, Any] = {
        "number": pr,
        "body": issue_body,
        "updated_at": created_at,
    }
    if pull_request:
        issue["pull_request"] = {"url": "https://example.invalid/pr"}
    return {
        "action": action,
        "repository": {"id": repository_id, "full_name": repository},
        "issue": issue,
        "comment": {
            "id": comment_id,
            "body": body,
            "user": {"login": login, "type": user_type},
            "author_association": association,
            "created_at": created_at,
        },
        "sender": {"login": login, "type": user_type},
    }


def reach_clawsweeper_findings(temp: Path, state: Path, pr: int, head: str, *, run_id: int) -> dict[str, Any]:
    legacy.github_event(temp, state, "pull_request", f"pr-{pr}-{head[:8]}", legacy.pr_payload(pr, head))
    legacy.github_event(
        temp, state, "workflow_run", f"ci-{pr}-{head[:8]}", legacy.workflow_payload(pr, head, "success", run_id)
    )
    request_id = legacy.dispatch_openclaw(temp, state, pr, head)
    legacy.internal_event(
        temp,
        state,
        f"oc-start-{pr}",
        legacy.openclaw_event(f"internal:oc-start-{pr}", "openclaw.started", pr, head, request_id),
    )
    legacy.internal_event(
        temp,
        state,
        f"oc-clean-{pr}",
        legacy.openclaw_event(
            f"internal:oc-clean-{pr}", "openclaw.terminal", pr, head, request_id, result="clean", findings=0
        ),
    )
    workflow_run_id = legacy.dispatch_clawsweeper(temp, state, pr, head)
    terminal = legacy.internal_event(
        temp,
        state,
        f"cs-findings-{pr}",
        legacy.clawsweeper_event(
            f"internal:cs-findings-{pr}", pr, head, workflow_run_id, result="findings", findings=2
        ),
    )
    assert terminal["state"] == "awaiting_adjudication"
    return {"request_id": request_id, "workflow_run_id": workflow_run_id}


def pending_kind(state: Path, pr: int, kind: str) -> list[dict[str, Any]]:
    current = legacy.status(state, pr)
    return [item for item in current["actions"] if item["kind"] == kind and item["status"] == "pending"]


def test_command_aliases_and_legacy_review_is_not_an_alias() -> None:
    assert core.parse_rereview_command("@ClawSweeper rereview") == "rereview"
    assert core.parse_rereview_command("please @clawsweeper re-review now") == "re-review"
    assert core.parse_rereview_command("@CLAWSWEEPER[bot] REREVIEW") == "rereview"
    assert core.parse_rereview_command("@clawsweeper review") is None
    assert core.parse_rereview_command("@clawsweeper re-run") is None
    assert core.parse_rereview_command("no mention") is None


def test_same_head_proof_refresh_and_stale_completion(temp: Path) -> None:
    state = temp / "state-same-head"
    head = "a" * 40
    first = reach_clawsweeper_findings(temp, state, 301, head, run_id=1301)
    first_status = legacy.status(state, 301)
    first_projection = first_status["projection"]
    first_source_updated_at = first_status["head"]["source_updated_at"]
    assert first_projection["checks"]["ClawSweeper Review Rail"] == "action_required"
    accepted = legacy.github_event(
        temp,
        state,
        "issue_comment",
        "rereview-same-head",
        comment_payload(301, "@ClawSweeper rereview", issue_body="added packaging proof"),
    )
    assert accepted["result"] == "accepted"
    assert accepted["state"] == "clawsweeper_queued"
    assert accepted["rereview_attempt"] == 2
    assert accepted["merge_dispatched"] is False
    assert "attempt 2" in accepted["acknowledgement"]
    assert pending_kind(state, 301, "clawsweeper.dispatch")
    refreshed = pending_kind(state, 301, "clawsweeper.dispatch")[0]
    assert refreshed["payload"]["evidence_refreshed"] is True
    assert refreshed["payload"]["previous_workflow_run_id"] == str(first["workflow_run_id"])
    assert refreshed["payload"]["head_sha"] == head
    current = legacy.status(state, 301)
    assert current["head"]["review_epoch"] == accepted["review_epoch"]
    assert current["head"]["source_updated_at"] == first_source_updated_at
    assert current["projection"]["checks"]["ClawSweeper Review Rail"] == "queued"
    assert current["projection"]["merge_authorized"] is False
    dispatches = [item for item in current["actions"] if item["kind"] == "clawsweeper.dispatch"]
    assert {item["status"] for item in dispatches} == {"obsolete", "pending"}
    superseded = next(item for item in dispatches if item["status"] == "obsolete")
    assert superseded["payload"].get("rereview_attempt") is None
    assert superseded["last_error"] == "superseded by maintainer rereview"

    stale = legacy.internal_event(
        temp,
        state,
        "stale-old-terminal",
        legacy.clawsweeper_event(
            "internal:stale-old-terminal",
            301,
            head,
            first["workflow_run_id"],
            result="findings",
            findings=2,
        ),
    )
    assert stale["result"] == "stale"
    assert legacy.status(state, 301)["head"]["state"] == "clawsweeper_queued"

    adapter_root = temp / "rereview-dispatch"
    adapter_root.mkdir()
    _, fake_gh, log = legacy.create_fake_adapters(adapter_root)
    dispatched = legacy.run(
        "dispatch-action",
        "--config",
        str(legacy.CONFIG),
        "--state-root",
        str(state),
        "--action-id",
        refreshed["action_id"],
        "--apply",
        env={
            "SMOKY_REVIEW_CONDUCTOR_GH": str(fake_gh),
            "FAKE_ADAPTER_LOG": str(log),
            "FAKE_HEAD": head,
        },
    )
    assert dispatched["result"] == "dispatched"
    clean = legacy.internal_event(
        temp,
        state,
        "fresh-clean",
        legacy.clawsweeper_event("internal:fresh-clean", 301, head, 9901, result="clean", findings=0),
    )
    assert clean["state"] == "ready_for_human_merge"
    final = legacy.status(state, 301)
    assert final["projection"]["checks"]["ClawSweeper Review Rail"] == "success"
    assert final["projection"]["merge_authorized"] is False


def test_rereview_keeps_pr_source_watermark_and_uncertain_ack(temp: Path) -> None:
    state = temp / "state-watermark"
    head = "a" * 40
    reach_clawsweeper_findings(temp, state, 307, head, run_id=1307)
    before = legacy.status(state, 307)["head"]["source_updated_at"]
    accepted = legacy.github_event(
        temp,
        state,
        "issue_comment",
        "rereview-watermark",
        comment_payload(307, "@ClawSweeper rereview", comment_id=9401),
    )
    assert accepted["result"] == "accepted"
    assert legacy.status(state, 307)["head"]["source_updated_at"] == before

    root = temp / "uncertain-ack"
    root.mkdir()
    config = userland_tests.config_fixture(root)
    userland_tests.ingress(config, "pull_request", "ack-pr", userland_tests.pr_payload(8, userland_tests.HEAD))
    userland_tests.ingress(config, "workflow_run", "ack-ci", userland_tests.ci_payload(8, 1801, userland_tests.HEAD))
    openclaw = userland_tests.action(config, 8, "openclaw.enqueue")
    userland_tests.mark_dispatched(config, openclaw["action_id"])
    request_id = json.loads(openclaw["payload_json"])["queue_request_id"]
    core.ingest_internal_event(
        config_path=Path(config["core_config"]),
        state_root=Path(config["paths"]["state_root"]),
        event_payload={
            "schema": "smoky.review-conductor.event.v1",
            "event_id": "ack-oc-clean",
            "type": "openclaw.terminal",
            "repository": "dinkuskit/blocks",
            "pr_number": 8,
            "base_sha": userland_tests.BASE,
            "head_sha": userland_tests.HEAD,
            "request_id": request_id,
            "result": "clean",
            "finding_count": 0,
            "reviewer_actor": "openclaw-reviewer",
            "proof_ref": "proof/openclaw/ack/PROOF.md",
        },
    )
    claw = userland_tests.action(config, 8, "clawsweeper.dispatch")
    userland_tests.mark_dispatched(config, claw["action_id"])
    core.ingest_internal_event(
        config_path=Path(config["core_config"]),
        state_root=Path(config["paths"]["state_root"]),
        event_payload={
            "schema": "smoky.review-conductor.event.v1",
            "event_id": "ack-cs-findings",
            "type": "clawsweeper.terminal",
            "repository": "dinkuskit/blocks",
            "pr_number": 8,
            "base_sha": userland_tests.BASE,
            "head_sha": userland_tests.HEAD,
            "workflow_run_id": 4343,
            "result": "findings",
            "finding_count": 2,
            "reviewer_actor": "saari-clawsweeper",
            "proof_ref": "proof/clawsweeper/ack/PROOF.md",
        },
    )
    userland_tests.ingress(
        config,
        "issue_comment",
        "ack-rereview",
        comment_payload(8, "@ClawSweeper rereview", comment_id=9402),
    )

    class RaisingClient:
        def create_issue_comment(self, pr: int, body: str, **_kwargs: Any) -> int:
            raise RuntimeError("transport lost after accept")

        def remove_ready_label(self, pr: int, **_kwargs: Any) -> None:
            return None

        def dispatch_clawsweeper(self, **kwargs: Any) -> None:
            return None

    try:
        runtime.drain_actions(config, RaisingClient())
    except RuntimeError as exc:
        assert "transport lost after accept" in str(exc)
    else:
        raise AssertionError("uncertain acknowledgement must surface the transport error")
    connection = core.open_database(Path(config["paths"]["state_root"]))
    try:
        ack = connection.execute(
            "SELECT status, last_error FROM actions WHERE kind = 'rereview.acknowledge'"
        ).fetchone()
        connection.execute(
            """
            UPDATE actions SET status = 'dispatching',
              lease_expires_at = '2020-01-01T00:00:00Z', updated_at = ?
            WHERE kind = 'rereview.acknowledge'
            """,
            (core.utc_now(),),
        )
        connection.commit()
    finally:
        connection.close()
    assert ack["status"] == "reconcile_required"
    assert "do not resend" in ack["last_error"]
    recovered = runtime.recover_abandoned_actions(config)
    assert any(
        item["kind"] == "rereview.acknowledge" and item["status"] == "reconcile_required"
        for item in recovered
    )

    class RecordingClient:
        def __init__(self) -> None:
            self.comments: list[str] = []

        def create_issue_comment(self, pr: int, body: str, **_kwargs: Any) -> int:
            self.comments.append(body)
            return 88

        def remove_ready_label(self, pr: int, **_kwargs: Any) -> None:
            return None

        def dispatch_clawsweeper(self, **kwargs: Any) -> None:
            return None

    client = RecordingClient()
    runtime.drain_actions(config, client)
    assert client.comments == []


def test_new_head_requires_current_ci_and_openclaw(temp: Path) -> None:
    state = temp / "state-new-head"
    old = "b" * 40
    new = "c" * 40
    reach_clawsweeper_findings(temp, state, 302, old, run_id=1302)
    legacy.github_event(temp, state, "pull_request", "sync-new-head", legacy.pr_payload(302, new, "synchronize"))
    assert legacy.status(state, 302)["head"]["state"] == "ci_running"
    waiting = legacy.github_event(
        temp,
        state,
        "issue_comment",
        "rereview-too-early",
        comment_payload(302, "@clawsweeper re-review", comment_id=9002),
    )
    assert waiting["result"] == "waiting"
    assert "CI and comprehensive OpenClaw" in waiting["reason"]
    assert waiting["acknowledgement"]
    assert not pending_kind(state, 302, "clawsweeper.dispatch")


def test_unauthorized_bot_non_pr_and_legacy_review(temp: Path) -> None:
    state = temp / "state-auth"
    head = "d" * 40
    reach_clawsweeper_findings(temp, state, 303, head, run_id=1303)
    contributor = legacy.github_event(
        temp,
        state,
        "issue_comment",
        "contributor-rereview",
        comment_payload(303, "@ClawSweeper rereview", comment_id=9101, association="CONTRIBUTOR", login="contributor"),
    )
    assert contributor["result"] == "refused"
    assert contributor["reason"] == "requester is not an authoritative maintainer"
    assert "acknowledgement" not in contributor
    bot = legacy.github_event(
        temp,
        state,
        "issue_comment",
        "bot-rereview",
        comment_payload(303, "@ClawSweeper rereview", comment_id=9102, login="saari-clawsweeper", user_type="Bot"),
    )
    assert bot["result"] == "refused"
    assert bot["reason"] == "requester is not a human user"
    non_pr = legacy.github_event(
        temp,
        state,
        "issue_comment",
        "issue-only",
        comment_payload(303, "@ClawSweeper rereview", comment_id=9103, pull_request=False),
    )
    assert non_pr["result"] == "ignored"
    assert "not on a pull request" in non_pr["reason"]
    edited = legacy.github_event(
        temp,
        state,
        "issue_comment",
        "edited-comment",
        comment_payload(303, "@ClawSweeper rereview", comment_id=9104, action="edited"),
    )
    assert edited["result"] == "ignored"
    first_pass = legacy.github_event(
        temp,
        state,
        "issue_comment",
        "legacy-review",
        comment_payload(303, "@clawsweeper review", comment_id=9105),
    )
    assert first_pass["result"] == "ignored"
    assert "not a documented" in first_pass["reason"]
    assert legacy.status(state, 303)["head"]["state"] == "awaiting_adjudication"


def test_duplicate_delivery_and_in_flight_wait(temp: Path) -> None:
    state = temp / "state-dup"
    head = "e" * 40
    reach_clawsweeper_findings(temp, state, 304, head, run_id=1304)
    payload = comment_payload(304, "@ClawSweeper rereview", comment_id=9201)
    first = legacy.github_event(temp, state, "issue_comment", "dup-delivery", payload)
    assert first["result"] == "accepted"
    replay = legacy.github_event(temp, state, "issue_comment", "dup-delivery", payload)
    assert replay["result"] == "duplicate_delivery"
    same_comment = legacy.github_event(
        temp,
        state,
        "issue_comment",
        "same-comment-new-delivery",
        payload,
    )
    assert same_comment["result"] == "duplicate"
    inflight = legacy.github_event(
        temp,
        state,
        "issue_comment",
        "second-command",
        comment_payload(304, "@clawsweeper re-review", comment_id=9202, created_at="2026-08-27T01:05:00Z"),
    )
    assert inflight["result"] == "waiting"
    assert "already in flight" in inflight["reason"]
    assert len(pending_kind(state, 304, "clawsweeper.dispatch")) == 1


def test_exhausted_repair_budget_refuses_rereview(temp: Path) -> None:
    state = temp / "state-budget"
    heads = ["4" * 40, "5" * 40, "6" * 40]
    for index, head in enumerate(heads):
        action = "opened" if index == 0 else "synchronize"
        legacy.github_event(
            temp, state, "pull_request", f"budget-pr-{index}", legacy.pr_payload(305, head, action)
        )
        legacy.github_event(
            temp,
            state,
            "workflow_run",
            f"budget-ci-{index}",
            legacy.workflow_payload(305, head, "success", 1400 + index),
        )
        request_id = legacy.dispatch_openclaw(temp, state, 305, head)
        legacy.internal_event(
            temp,
            state,
            f"budget-findings-{index}",
            legacy.openclaw_event(
                f"internal:budget-findings-{index}",
                "openclaw.terminal",
                305,
                head,
                request_id,
                result="findings",
                findings=1,
            ),
        )
        adjudicated = legacy.internal_event(
            temp,
            state,
            f"budget-adjudication-{index}",
            legacy.adjudication_event(
                f"internal:budget-adjudication-{index}", 305, head, request_id, ["required_fix"]
            ),
        )
    assert adjudicated["state"] == "waiting_human"
    assert legacy.status(state, 305)["head"]["repair_cycle"] == 2
    refused = legacy.github_event(
        temp,
        state,
        "issue_comment",
        "budget-rereview",
        comment_payload(305, "@ClawSweeper rereview", comment_id=9301),
    )
    assert refused["result"] == "refused"
    assert refused["reason"] == "two automatic repair cycles exhausted"
    assert "acknowledgement" in refused
    assert legacy.status(state, 305)["projection"]["merge_authorized"] is False


def test_body_edit_remains_unsupported(temp: Path) -> None:
    state = temp / "state-edited"
    head = "f" * 40
    legacy.github_event(temp, state, "pull_request", "edited-pr", legacy.pr_payload(306, head))
    path = legacy.write_json(temp, "edited-action.json", legacy.pr_payload(306, head, "edited"))
    failed = legacy.run(
        "github-event",
        "--config",
        str(legacy.CONFIG),
        "--state-root",
        str(state),
        "--event-type",
        "pull_request",
        "--delivery-id",
        "edited-action",
        "--signature",
        legacy.signature(path),
        "--body-file",
        str(path),
        expected=2,
    )
    assert "unsupported pull_request action edited" in failed["stderr"]


def test_end_to_end_command_to_current_check(temp: Path) -> None:
    root = temp / "e2e-userland"
    root.mkdir()
    config = userland_tests.config_fixture(root)
    head = userland_tests.HEAD
    userland_tests.ingress(config, "pull_request", "e2e-pr", userland_tests.pr_payload(7, head))
    userland_tests.ingress(config, "workflow_run", "e2e-ci", userland_tests.ci_payload(7, 1701, head))
    openclaw = userland_tests.action(config, 7, "openclaw.enqueue")
    userland_tests.mark_dispatched(config, openclaw["action_id"])
    request_id = json.loads(openclaw["payload_json"])["queue_request_id"]
    core.ingest_internal_event(
        config_path=Path(config["core_config"]),
        state_root=Path(config["paths"]["state_root"]),
        event_payload={
            "schema": "smoky.review-conductor.event.v1",
            "event_id": "e2e-oc-start",
            "type": "openclaw.started",
            "repository": "dinkuskit/blocks",
            "pr_number": 7,
            "base_sha": userland_tests.BASE,
            "head_sha": head,
            "request_id": request_id,
        },
    )
    core.ingest_internal_event(
        config_path=Path(config["core_config"]),
        state_root=Path(config["paths"]["state_root"]),
        event_payload={
            "schema": "smoky.review-conductor.event.v1",
            "event_id": "e2e-oc-clean",
            "type": "openclaw.terminal",
            "repository": "dinkuskit/blocks",
            "pr_number": 7,
            "base_sha": userland_tests.BASE,
            "head_sha": head,
            "request_id": request_id,
            "result": "clean",
            "finding_count": 0,
            "reviewer_actor": "openclaw-reviewer",
            "proof_ref": "proof/openclaw/e2e/PROOF.md",
        },
    )
    claw = userland_tests.action(config, 7, "clawsweeper.dispatch")
    userland_tests.mark_dispatched(config, claw["action_id"])
    core.ingest_internal_event(
        config_path=Path(config["core_config"]),
        state_root=Path(config["paths"]["state_root"]),
        event_payload={
            "schema": "smoky.review-conductor.event.v1",
            "event_id": "e2e-cs-findings",
            "type": "clawsweeper.terminal",
            "repository": "dinkuskit/blocks",
            "pr_number": 7,
            "base_sha": userland_tests.BASE,
            "head_sha": head,
            "workflow_run_id": 4242,
            "result": "findings",
            "finding_count": 2,
            "reviewer_actor": "saari-clawsweeper",
            "proof_ref": "proof/clawsweeper/e2e/PROOF.md",
        },
    )
    receipt = userland_tests.ingress(
        config,
        "issue_comment",
        "e2e-rereview",
        comment_payload(7, "@ClawSweeper rereview"),
    )
    assert receipt["result"] == "accepted"

    class FakeClient:
        def __init__(self) -> None:
            self.comments: list[str] = []
            self.dispatches: list[dict[str, Any]] = []
            self.next_comment = 77

        def create_issue_comment(self, pr: int, body: str, **_kwargs: Any) -> int:
            assert pr == 7
            self.comments.append(body)
            self.next_comment += 1
            return self.next_comment

        def remove_ready_label(self, pr: int, **_kwargs: Any) -> None:
            return None

        def dispatch_clawsweeper(self, **kwargs: Any) -> None:
            self.dispatches.append(kwargs)

    client = FakeClient()
    drained = runtime.drain_actions(config, client)
    assert drained["merge_dispatched"] is False
    assert client.comments
    assert "accepted @ClawSweeper rereview" in client.comments[0]
    assert client.dispatches
    connection = core.open_database(Path(config["paths"]["state_root"]))
    try:
        row = dict(core.current_head(connection, "dinkuskit/blocks", 7))
    finally:
        connection.close()
    projection = core.state_projection(row)
    assert projection["checks"]["ClawSweeper Review Rail"] == "queued"
    assert projection["merge_authorized"] is False


def test_app_events_include_issue_comment() -> None:
    assert runtime.APP_EVENTS == ["pull_request", "workflow_run", "issue_comment"]
    assert "issue_comment" in core.GITHUB_EVENT_TYPES


def main() -> int:
    selected = {argument for argument in sys.argv[1:] if not argument.startswith("-")}
    named = {
        "test_command_aliases_and_legacy_review_is_not_an_alias": lambda _temp: test_command_aliases_and_legacy_review_is_not_an_alias(),
        "test_same_head_proof_refresh_and_stale_completion": test_same_head_proof_refresh_and_stale_completion,
        "test_rereview_keeps_pr_source_watermark_and_uncertain_ack": test_rereview_keeps_pr_source_watermark_and_uncertain_ack,
        "test_new_head_requires_current_ci_and_openclaw": test_new_head_requires_current_ci_and_openclaw,
        "test_unauthorized_bot_non_pr_and_legacy_review": test_unauthorized_bot_non_pr_and_legacy_review,
        "test_duplicate_delivery_and_in_flight_wait": test_duplicate_delivery_and_in_flight_wait,
        "test_exhausted_repair_budget_refuses_rereview": test_exhausted_repair_budget_refuses_rereview,
        "test_body_edit_remains_unsupported": test_body_edit_remains_unsupported,
        "test_end_to_end_command_to_current_check": test_end_to_end_command_to_current_check,
        "test_app_events_include_issue_comment": lambda _temp: test_app_events_include_issue_comment(),
    }
    if selected - set(named):
        raise SystemExit(f"unknown clawsweeper rereview tests: {sorted(selected - set(named))}")
    names = [name for name in named if not selected or name in selected]
    with tempfile.TemporaryDirectory(prefix="review-conductor-rereview-") as temp_name:
        temp = Path(temp_name)
        for name in names:
            named[name](temp)
    print("clawsweeper rereview tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
