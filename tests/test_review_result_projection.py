#!/usr/bin/env python3
"""Trusted GitHub projection: content vs process, checks, and publication."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
import review_result_projection as projection  # noqa: E402
import review_conductor_runtime as runtime  # noqa: E402


REPO = "saari-co/openclaw-smcbd-suite"
BASE = "d50ec1671418aaf3ee1aef2020c9f2d20e4b6a18"
HEAD = "ed1c53d69ea9a798e330bf7eb71e9a401feb81b5"
DIGEST = "b9f1121b03edb514b75ae5211b5f1b9120d05d704cb9d8f8bae073f4e6158d84"
RUN_ID = "34898456780"


def identity(**overrides: Any) -> dict[str, Any]:
    value = {
        "repository": REPO,
        "pr_number": 19,
        "base_sha": BASE,
        "head_sha": HEAD,
        "review_epoch": 0,
        "issuer_app_id": projection.CONDUCTOR_ISSUER_APP_ID,
        "artifact_digest": DIGEST,
    }
    value.update(overrides)
    return value


def expected_bind(**overrides: Any) -> dict[str, Any]:
    return identity(**overrides)


class RecordingGitHub:
    def __init__(self) -> None:
        self.comments: list[dict[str, Any]] = []
        self.labels: list[str] = ["P3", "docs"]
        self.next_id = 900
        self.calls: list[tuple[Any, ...]] = []

    def create_issue_comment(self, pr: int, body: str) -> int:
        self.next_id += 1
        self.comments.append({"id": self.next_id, "body": body, "pr": pr})
        self.calls.append(("create_comment", pr))
        return self.next_id

    def update_issue_comment(self, comment_id: int, body: str) -> None:
        for item in self.comments:
            if item["id"] == comment_id:
                item["body"] = body
        self.calls.append(("update_comment", comment_id))

    def add_owned_label(self, pr: int, name: str) -> None:
        if name not in self.labels:
            self.labels.append(name)
        self.calls.append(("add_label", pr, name))

    def remove_owned_label(self, pr: int, name: str) -> None:
        self.labels = [item for item in self.labels if item != name]
        self.calls.append(("remove_label", pr, name))


def test_clean_completed_review_is_success_while_merge_stays_human() -> None:
    classified = projection.classify_native_review(
        review_status="complete",
        terminal_failure=False,
        finding_count=0,
        maintainer_required=False,
        overall_tier="B",
        proof_status="not_applicable",
        needs_contributor_action=False,
        decision="keep_open",
        process_gates=["own_current_check", "owner_merge_authority"],
    )
    assert classified["content_verdict"] == "clean"
    assert classified["rail_result"] == "clean"
    assert classified["merge_authorized"] is False
    assert classified["own_current_check_circular"] is True
    assert classified["process_gates"] == [
        "own_current_check",
        "owner_merge_authority",
    ]


def test_keep_open_is_not_blindly_success() -> None:
    classified = projection.classify_native_review(
        review_status="complete",
        terminal_failure=False,
        finding_count=1,
        maintainer_required=False,
        overall_tier="B",
        proof_status="sufficient",
        decision="keep_open",
    )
    assert classified["content_verdict"] == "findings"
    assert classified["rail_result"] == "findings"
    assert classified["process_gates"] == []


def test_execution_failure_and_human_policy_stay_distinct() -> None:
    failed = projection.classify_native_review(
        review_status="complete",
        terminal_failure=True,
        finding_count=0,
        maintainer_required=False,
        overall_tier="B",
        proof_status="sufficient",
    )
    policy = projection.classify_native_review(
        review_status="complete",
        terminal_failure=False,
        finding_count=0,
        maintainer_required=True,
        overall_tier="B",
        proof_status="sufficient",
    )
    proof = projection.classify_native_review(
        review_status="complete",
        terminal_failure=False,
        finding_count=0,
        maintainer_required=False,
        overall_tier="B",
        proof_status="insufficient",
        needs_contributor_action=True,
    )
    assert failed["content_verdict"] == "failed"
    assert failed["rail_result"] == "failed"
    assert policy["content_verdict"] == "human_policy"
    assert policy["rail_result"] == "human_gate"
    assert proof["content_verdict"] == "proof_deficient"
    assert proof["rail_result"] == "human_gate"


def test_legacy_ready_fixture_stays_clean() -> None:
    classified = projection.classify_native_review(
        review_status="complete",
        terminal_failure=False,
        finding_count=0,
        maintainer_required=False,
        overall_tier="B",
        proof_status="sufficient",
    )
    assert classified["rail_result"] == "clean"
    assert classified["process_gates"] == ["owner_merge_authority"]


def test_stale_and_mismatched_artifacts_are_rejected_before_write() -> None:
    expected = expected_bind()
    try:
        projection.bind_accepted_artifact(
            repository=REPO,
            pr_number=19,
            base_sha=BASE,
            head_sha="0" * 40,
            review_epoch=0,
            issuer_app_id=projection.CONDUCTOR_ISSUER_APP_ID,
            artifact_digest=DIGEST,
            expected=expected,
        )
    except projection.ProjectionError as exc:
        assert "stale or mismatched" in str(exc)
    else:
        raise AssertionError("mismatched head must be rejected")
    try:
        projection.bind_accepted_artifact(
            repository=REPO,
            pr_number=19,
            base_sha=BASE,
            head_sha=HEAD,
            review_epoch=0,
            issuer_app_id=1,
            artifact_digest=DIGEST,
            expected=expected,
        )
    except projection.ProjectionError as exc:
        assert "issuer" in str(exc)
    else:
        raise AssertionError("foreign issuer must be rejected")
    try:
        projection.bind_accepted_artifact(
            repository=REPO,
            pr_number=19,
            base_sha=BASE,
            head_sha=HEAD,
            review_epoch=0,
            issuer_app_id=projection.CONDUCTOR_ISSUER_APP_ID,
            artifact_digest="a" * 64,
            expected={**expected, "artifact_digest": DIGEST},
        )
    except projection.ProjectionError as exc:
        assert "conflicts" in str(exc)
    else:
        raise AssertionError("replayed conflicting digest must be rejected")


def test_check_output_has_stage_head_run_and_digest() -> None:
    output = projection.check_output(
        "ClawSweeper Review Rail",
        "success",
        repository=REPO,
        pr_number=19,
        head_sha=HEAD,
        stage="ready_for_human_merge",
        content_verdict="clean",
        process_gates=["owner_merge_authority"],
        reason="human merge authority required",
        workflow_run_id=RUN_ID,
        artifact_digest=DIGEST,
    )
    assert output["details_url"] == f"https://github.com/{REPO}/actions/runs/{RUN_ID}"
    assert HEAD in output["summary"]
    assert DIGEST in output["summary"]
    assert "ready_for_human_merge" in output["title"]
    assert "human_only" in output["summary"]
    payload = runtime.check_payload(
        "ClawSweeper Review Rail",
        HEAD,
        "review-conductor:test",
        "success",
        report={
            "repository": REPO,
            "pr_number": 19,
            "stage": "ready_for_human_merge",
            "content_verdict": "clean",
            "process_gates": ["owner_merge_authority"],
            "reason": "human merge authority required",
            "workflow_run_id": RUN_ID,
            "artifact_digest": DIGEST,
        },
    )
    assert payload["status"] == "completed"
    assert payload["conclusion"] == "success"
    assert payload["details_url"] == output["details_url"]
    assert payload["output"]["summary"] == output["summary"]


def test_publication_is_idempotent_and_preserves_foreign_labels() -> None:
    bound = projection.bind_accepted_artifact(
        repository=REPO,
        pr_number=19,
        base_sha=BASE,
        head_sha=HEAD,
        review_epoch=0,
        issuer_app_id=projection.CONDUCTOR_ISSUER_APP_ID,
        artifact_digest=DIGEST,
        expected=expected_bind(),
    )
    classified = projection.classify_native_review(
        review_status="complete",
        terminal_failure=False,
        finding_count=0,
        maintainer_required=False,
        overall_tier="B",
        proof_status="not_applicable",
        decision="keep_open",
        process_gates=json.dumps(
            ["own_current_check", "owner_merge_authority"], separators=(",", ":")
        ),
    )
    client = RecordingGitHub()
    first = projection.plan_github_publication(
        identity=bound,
        classification=classified,
        existing_comments=client.comments,
        existing_labels=client.labels,
        stage="ready_for_human_merge",
        reason="human merge authority required",
        workflow_run_id=RUN_ID,
    )
    assert first["comment"]["action"] == "create"
    assert first["labels"]["preserve"] == ["P3", "docs"]
    assert first["labels"]["add"] == ["status: 👀 ready for maintainer look"]
    receipt = projection.apply_github_publication(client, first)
    assert receipt["comment_action"] == "create"
    second = projection.plan_github_publication(
        identity=bound,
        classification=classified,
        existing_comments=client.comments,
        existing_labels=client.labels,
        stage="ready_for_human_merge",
        reason="human merge authority required",
        workflow_run_id=RUN_ID,
    )
    assert second["comment"]["action"] == "unchanged"
    assert second["writes"] is False
    projection.apply_github_publication(client, second)
    assert len([call for call in client.calls if call[0] == "create_comment"]) == 1
    assert [call for call in client.calls if call[0] == "update_comment"] == []
    assert "P3" in client.labels and "docs" in client.labels


def test_unowned_label_mutation_is_rejected() -> None:
    bound = projection.bind_accepted_artifact(
        repository=REPO,
        pr_number=19,
        base_sha=BASE,
        head_sha=HEAD,
        review_epoch=0,
        issuer_app_id=projection.CONDUCTOR_ISSUER_APP_ID,
        artifact_digest=DIGEST,
        expected=expected_bind(),
    )
    classified = projection.classify_native_review(
        review_status="complete",
        terminal_failure=False,
        finding_count=1,
        maintainer_required=False,
        overall_tier="B",
        proof_status="sufficient",
    )
    plan = projection.plan_github_publication(
        identity=bound,
        classification=classified,
        existing_comments=[],
        existing_labels=["P3"],
        stage="awaiting_adjudication",
        reason="findings require bounded adjudication",
        workflow_run_id=RUN_ID,
    )
    plan["labels"]["remove"] = ["P3"]
    try:
        projection.apply_github_publication(RecordingGitHub(), plan)
    except projection.ProjectionError as exc:
        assert "unowned label" in str(exc)
    else:
        raise AssertionError("unowned label removal must fail closed")


def test_unknown_markdown_process_gate_is_rejected() -> None:
    try:
        projection.classify_native_review(
            review_status="complete",
            terminal_failure=False,
            finding_count=0,
            maintainer_required=False,
            overall_tier="B",
            proof_status="sufficient",
            process_gates='["merge_now"]',
        )
    except projection.ProjectionError as exc:
        assert "unsupported gate" in str(exc)
    else:
        raise AssertionError("unknown process gate must fail closed")


def main() -> int:
    tests = [
        test_clean_completed_review_is_success_while_merge_stays_human,
        test_keep_open_is_not_blindly_success,
        test_execution_failure_and_human_policy_stay_distinct,
        test_legacy_ready_fixture_stays_clean,
        test_stale_and_mismatched_artifacts_are_rejected_before_write,
        test_check_output_has_stage_head_run_and_digest,
        test_publication_is_idempotent_and_preserves_foreign_labels,
        test_unowned_label_mutation_is_rejected,
        test_unknown_markdown_process_gate_is_rejected,
    ]
    for test in tests:
        test()
    print(f"review result projection tests passed ({len(tests)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
