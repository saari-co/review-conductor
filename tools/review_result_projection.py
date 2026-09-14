#!/usr/bin/env python3
"""Trusted GitHub projection of accepted exact-tuple review results.

This module classifies native review content separately from merge
authorization, builds useful check output, and plans idempotent
comment/label writes. It never reads live credentials or treats
Markdown as a write command language.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any


CONTENT_CLEAN = "clean"
CONTENT_FINDINGS = "findings"
CONTENT_FAILED = "failed"
CONTENT_PROOF_DEFICIENT = "proof_deficient"
CONTENT_HUMAN_POLICY = "human_policy"
CONTENT_VERDICTS = frozenset(
    {
        CONTENT_CLEAN,
        CONTENT_FINDINGS,
        CONTENT_FAILED,
        CONTENT_PROOF_DEFICIENT,
        CONTENT_HUMAN_POLICY,
    }
)

PROCESS_OWN_CHECK = "own_current_check"
PROCESS_OWNER_MERGE = "owner_merge_authority"
KNOWN_PROCESS_GATES = frozenset({PROCESS_OWN_CHECK, PROCESS_OWNER_MERGE})

RAIL_RESULTS = frozenset({"clean", "findings", "failed", "human_gate"})
KNOWN_DECISIONS = frozenset({"keep_open", "close", "none"})
READY_OVERALL_TIERS = frozenset({"S", "A", "B"})
NONBLOCKING_PROOF_STATUSES = frozenset({"sufficient", "not_applicable", "not_needed"})
DEFICIENT_PROOF_STATUSES = frozenset({"insufficient", "missing", "failed", "required"})

CONDUCTOR_ISSUER_APP_ID = 4916376
CHECK_NAMES = ("OpenClaw Review Rail", "ClawSweeper Review Rail")
MARKER_PREFIX = "<!-- review-conductor:github-projection"

OWNED_LABELS = {
    "status: 👀 ready for maintainer look": CONTENT_CLEAN,
    "status: ⏳ waiting on author": CONTENT_FINDINGS,
    "status: 📣 needs proof": CONTENT_PROOF_DEFICIENT,
    "status: needs maintainer proof decision": CONTENT_HUMAN_POLICY,
}
LABEL_FOR_CONTENT = {kind: name for name, kind in OWNED_LABELS.items()}
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class ProjectionError(ValueError):
    """Artifact identity, classification, or publication plan is invalid."""


def require_sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or SHA_RE.fullmatch(value) is None:
        raise ProjectionError(f"{label} must be a lowercase full 40-character commit SHA")
    return value


def require_digest(value: Any, label: str) -> str:
    text = value
    if isinstance(text, str) and text.startswith("sha256:"):
        text = text[7:]
    if not isinstance(text, str) or SHA256_RE.fullmatch(text) is None:
        raise ProjectionError(f"{label} must be a 64-character lowercase hex digest")
    return text


def require_repository(value: Any) -> str:
    if not isinstance(value, str) or REPO_RE.fullmatch(value) is None:
        raise ProjectionError("repository must be an owner/name identity")
    return value


def require_pr_number(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ProjectionError("pull request number must be a positive integer")
    return value


def require_epoch(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ProjectionError("review epoch must be a non-negative integer")
    return value


def parse_process_gates(value: Any) -> tuple[str, ...] | None:
    if value is None or value == "":
        return None
    raw = value
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ProjectionError("process_gates must be a JSON array of known gates") from exc
    if not isinstance(raw, list) or any(not isinstance(item, str) for item in raw):
        raise ProjectionError("process_gates must be a JSON array of known gates")
    if len(raw) != len(set(raw)):
        raise ProjectionError("process_gates must be unique")
    unknown = [item for item in raw if item not in KNOWN_PROCESS_GATES]
    if unknown:
        raise ProjectionError("process_gates contains an unsupported gate")
    return tuple(sorted(raw))


def parse_optional_decision(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str) or value not in KNOWN_DECISIONS:
        raise ProjectionError("native decision must be keep_open, close, or none")
    return value


def _truthy_flag(value: Any) -> bool:
    return value in {True, "true", "True"}


def classify_native_review(
    *,
    review_status: str,
    terminal_failure: bool,
    finding_count: int,
    maintainer_required: bool,
    overall_tier: str,
    proof_status: str,
    needs_contributor_action: bool = False,
    decision: str | None = None,
    process_gates: Any = None,
) -> dict[str, Any]:
    """Separate review content from merge/process waiting.

    keep_open is never mapped to success by itself. A completed report with
    no findings, no execution failure, no genuine proof deficiency, and no
    explicit maintainer question can be review-success while merge stays
    human-only. The reviewer's own in-flight rail check is a typed process
    gate, not a content defect.
    """
    if review_status != "complete":
        raise ProjectionError("native review is not complete")
    if isinstance(finding_count, bool) or not isinstance(finding_count, int) or finding_count < 0:
        raise ProjectionError("finding_count must be a non-negative integer")
    decision = parse_optional_decision(decision)
    typed_gates = parse_process_gates(process_gates)

    if terminal_failure:
        return _classification(CONTENT_FAILED, (), "failed", decision)
    if finding_count > 0:
        return _classification(CONTENT_FINDINGS, (), "findings", decision)
    if maintainer_required:
        return _classification(CONTENT_HUMAN_POLICY, (), "human_gate", decision)
    if needs_contributor_action or proof_status in DEFICIENT_PROOF_STATUSES:
        return _classification(CONTENT_PROOF_DEFICIENT, (), "human_gate", decision)
    if proof_status not in NONBLOCKING_PROOF_STATUSES:
        raise ProjectionError("proof_status is not a validated non-blocking or deficient value")

    content_ready = overall_tier in READY_OVERALL_TIERS
    if not content_ready and typed_gates is None and decision != "keep_open":
        return _classification(CONTENT_HUMAN_POLICY, (), "human_gate", decision)

    if typed_gates is not None:
        gates = typed_gates
    else:
        inferred = [PROCESS_OWNER_MERGE]
        if decision == "keep_open":
            inferred.append(PROCESS_OWN_CHECK)
        gates = tuple(sorted(set(inferred)))
    if PROCESS_OWNER_MERGE not in gates:
        gates = tuple(sorted({*gates, PROCESS_OWNER_MERGE}))
    return _classification(CONTENT_CLEAN, gates, "clean", decision)


def _classification(
    content: str, process_gates: tuple[str, ...], rail_result: str, decision: str | None
) -> dict[str, Any]:
    if content not in CONTENT_VERDICTS or rail_result not in RAIL_RESULTS:
        raise ProjectionError("classification produced an unsupported verdict")
    return {
        "content_verdict": content,
        "process_gates": list(process_gates),
        "rail_result": rail_result,
        "merge_authorized": False,
        "merge_policy": "human_only",
        "own_current_check_circular": PROCESS_OWN_CHECK in process_gates,
        "native_decision": decision,
    }


def classify_from_frontmatter(frontmatter: dict[str, str], *, finding_count: int) -> dict[str, Any]:
    maintainer_required = False
    maintainer = frontmatter.get("maintainer_decision")
    if maintainer:
        try:
            payload = json.loads(maintainer)
        except json.JSONDecodeError as exc:
            raise ProjectionError("ClawSweeper maintainer decision is invalid") from exc
        maintainer_required = isinstance(payload, dict) and payload.get("required") is True
    return classify_native_review(
        review_status=frontmatter.get("review_status", ""),
        terminal_failure=_truthy_flag(frontmatter.get("review_terminal_failure")),
        finding_count=finding_count,
        maintainer_required=maintainer_required,
        overall_tier=frontmatter.get("pr_rating_overall", ""),
        proof_status=frontmatter.get("real_behavior_proof_status", ""),
        needs_contributor_action=_truthy_flag(
            frontmatter.get("real_behavior_proof_needs_contributor_action")
        ),
        decision=frontmatter.get("decision"),
        process_gates=frontmatter.get("process_gates"),
    )


def bind_accepted_artifact(
    *,
    repository: str,
    pr_number: int,
    base_sha: str,
    head_sha: str,
    review_epoch: int,
    issuer_app_id: int,
    artifact_digest: str,
    expected: dict[str, Any],
) -> dict[str, Any]:
    identity = {
        "repository": require_repository(repository),
        "pr_number": require_pr_number(pr_number),
        "base_sha": require_sha(base_sha, "base_sha"),
        "head_sha": require_sha(head_sha, "head_sha"),
        "review_epoch": require_epoch(review_epoch),
        "issuer_app_id": issuer_app_id,
        "artifact_digest": require_digest(artifact_digest, "artifact_digest"),
    }
    if issuer_app_id != expected.get("issuer_app_id"):
        raise ProjectionError("artifact issuer is not the trusted Conductor writer")
    for key in ("repository", "pr_number", "base_sha", "head_sha", "review_epoch"):
        if identity[key] != expected[key]:
            raise ProjectionError("artifact identity is stale or mismatched")
    previous = expected.get("artifact_digest")
    if previous and previous != identity["artifact_digest"]:
        raise ProjectionError("artifact digest conflicts with the accepted exact tuple")
    return identity


def workflow_run_url(repository: str, workflow_run_id: Any) -> str | None:
    if workflow_run_id in {None, ""}:
        return None
    run_id = str(workflow_run_id)
    if not run_id.isdigit() or run_id.startswith("0"):
        raise ProjectionError("workflow_run_id must be a positive integer string")
    return f"https://github.com/{require_repository(repository)}/actions/runs/{run_id}"


def pull_request_url(repository: str, pr_number: int) -> str:
    return f"https://github.com/{require_repository(repository)}/pull/{require_pr_number(pr_number)}"


def check_output(
    name: str,
    state: str,
    *,
    repository: str,
    pr_number: int,
    head_sha: str,
    stage: str | None = None,
    content_verdict: str | None = None,
    process_gates: list[str] | None = None,
    reason: str | None = None,
    workflow_run_id: Any = None,
    artifact_digest: str | None = None,
) -> dict[str, Any]:
    if name not in CHECK_NAMES:
        raise ProjectionError("check name is outside the fixed allowlist")
    require_sha(head_sha, "check head_sha")
    current_stage = stage or _stage_for_check_state(state)
    title = f"{name}: {current_stage}"
    lines = [
        f"Stage: {current_stage}.",
        f"Exact head: {head_sha}.",
    ]
    if content_verdict:
        if content_verdict not in CONTENT_VERDICTS:
            raise ProjectionError("check content verdict is unsupported")
        lines.append(f"Review content: {content_verdict}.")
    if process_gates:
        unknown = [item for item in process_gates if item not in KNOWN_PROCESS_GATES]
        if unknown:
            raise ProjectionError("check process gates are unsupported")
        lines.append(f"Process gates: {', '.join(process_gates)}.")
    lines.append("Merge authorized: no (human_only).")
    if reason:
        if "\n" in reason or len(reason) > 240:
            raise ProjectionError("check reason must be one bounded line")
        lines.append(f"Decision: {reason}")
    run_url = workflow_run_url(repository, workflow_run_id)
    if run_url:
        lines.append(f"Workflow run: {run_url}.")
    if artifact_digest:
        digest = require_digest(artifact_digest, "artifact_digest")
        lines.append(f"Accepted artifact: sha256:{digest}.")
    details = run_url or pull_request_url(repository, pr_number)
    return {
        "title": title,
        "summary": " ".join(lines),
        "text": "\n".join(lines),
        "details_url": details,
    }


def _stage_for_check_state(state: str) -> str:
    return {
        "queued": "waiting for exact prerequisite or dispatch",
        "in_progress": "running",
        "success": "completed",
        "failure": "terminal failure",
        "action_required": "human or contributor action required",
        "skipped": "not dispatched",
        "cancelled": "cancelled",
    }.get(state, state)


def comment_marker(identity: dict[str, Any]) -> str:
    digest = identity["artifact_digest"]
    if not str(digest).startswith("sha256:"):
        digest = f"sha256:{digest}"
    return (
        f"{MARKER_PREFIX} repo={identity['repository']} pr={identity['pr_number']} "
        f"base={identity['base_sha']} head={identity['head_sha']} "
        f"epoch={identity['review_epoch']} issuer={identity['issuer_app_id']} "
        f"artifact={digest} -->"
    )


def render_projection_comment(
    identity: dict[str, Any],
    classification: dict[str, Any],
    *,
    stage: str,
    reason: str,
    workflow_run_id: Any = None,
) -> str:
    run_url = workflow_run_url(identity["repository"], workflow_run_id)
    digest = identity["artifact_digest"]
    if not str(digest).startswith("sha256:"):
        digest = f"sha256:{digest}"
    gates = classification["process_gates"] or ["none"]
    body = "\n".join(
        [
            "## Review Conductor projection",
            "",
            f"- Stage: {stage}",
            f"- Review content: {classification['content_verdict']}",
            f"- Process gates: {', '.join(gates)}",
            "- Merge authorized: no (human_only)",
            f"- Exact head: `{identity['head_sha']}`",
            f"- Base: `{identity['base_sha']}`",
            f"- Epoch: {identity['review_epoch']}",
            f"- Issuer: App {identity['issuer_app_id']}",
            f"- Workflow run: {run_url or 'none'}",
            f"- Accepted artifact: `{digest}`",
            f"- Decision: {reason}",
            "",
            comment_marker(identity),
            "",
        ]
    )
    return body


def desired_owned_labels(content_verdict: str) -> set[str]:
    if content_verdict not in CONTENT_VERDICTS:
        raise ProjectionError("label content verdict is unsupported")
    name = LABEL_FOR_CONTENT.get(content_verdict)
    return {name} if name else set()


def plan_github_publication(
    *,
    identity: dict[str, Any],
    classification: dict[str, Any],
    existing_comments: list[dict[str, Any]],
    existing_labels: list[str],
    stage: str,
    reason: str,
    workflow_run_id: Any = None,
) -> dict[str, Any]:
    if classification["merge_authorized"] is not False:
        raise ProjectionError("publication cannot authorize merge")
    body = render_projection_comment(
        identity, classification, stage=stage, reason=reason, workflow_run_id=workflow_run_id
    )
    marker = comment_marker(identity)
    owned = [item for item in existing_comments if marker_matches_tuple(item.get("body", ""), identity)]
    if len(owned) > 1:
        raise ProjectionError("duplicate Conductor projection comments exist")
    comment_action = "create"
    comment_id = None
    if owned:
        comment_id = owned[0].get("id")
        if not isinstance(comment_id, int) or comment_id < 1:
            raise ProjectionError("existing projection comment id is invalid")
        if owned[0].get("body") == body:
            comment_action = "unchanged"
        else:
            comment_action = "update"
    desired = desired_owned_labels(classification["content_verdict"])
    present_owned = {name for name in existing_labels if name in OWNED_LABELS}
    foreign = [name for name in existing_labels if name not in OWNED_LABELS]
    return {
        "schema": "smoky.review-conductor.github-publication.v1",
        "identity": identity,
        "classification": classification,
        "comment": {
            "action": comment_action,
            "id": comment_id,
            "body": body,
            "marker": marker,
        },
        "labels": {
            "add": sorted(desired - present_owned),
            "remove": sorted(present_owned - desired),
            "preserve": sorted(foreign),
        },
        "writes": comment_action != "unchanged" or bool(desired - present_owned) or bool(present_owned - desired),
    }


def marker_matches_tuple(body: str, identity: dict[str, Any]) -> bool:
    if not isinstance(body, str) or MARKER_PREFIX not in body:
        return False
    required = (
        f"repo={identity['repository']}",
        f"pr={identity['pr_number']}",
        f"base={identity['base_sha']}",
        f"head={identity['head_sha']}",
        f"epoch={identity['review_epoch']}",
        f"issuer={identity['issuer_app_id']}",
    )
    return all(token in body for token in required)


def publication_body_digest(body: str) -> str:
    return hashlib.sha256(body.encode()).hexdigest()


def apply_github_publication(client: Any, plan: dict[str, Any]) -> dict[str, Any]:
    """Apply a previously validated plan through an injected GitHub client."""
    if plan.get("schema") != "smoky.review-conductor.github-publication.v1":
        raise ProjectionError("publication plan schema is unsupported")
    identity = plan["identity"]
    comment = plan["comment"]
    labels = plan["labels"]
    for name in [*labels["add"], *labels["remove"]]:
        if name not in OWNED_LABELS:
            raise ProjectionError("publication attempted to alter an unowned label")
    if comment["action"] == "create":
        comment_id = client.create_issue_comment(identity["pr_number"], comment["body"])
    elif comment["action"] == "update":
        comment_id = comment["id"]
        client.update_issue_comment(comment_id, comment["body"])
    else:
        comment_id = comment["id"]
    for name in labels["add"]:
        client.add_owned_label(identity["pr_number"], name)
    for name in labels["remove"]:
        client.remove_owned_label(identity["pr_number"], name)
    return {
        "schema": "smoky.review-conductor.github-publication-receipt.v1",
        "comment_id": comment_id,
        "comment_action": comment["action"],
        "labels_added": list(labels["add"]),
        "labels_removed": list(labels["remove"]),
        "labels_preserved": list(labels["preserve"]),
        "body_digest": publication_body_digest(comment["body"]),
        "artifact_digest": identity["artifact_digest"],
    }
