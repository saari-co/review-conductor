---
repository: example/synthetic-review
number: 7
main_sha: 1111111111111111111111111111111111111111
pull_head_sha: 2222222222222222222222222222222222222222
review_epoch: 0
review_scope: comprehensive
reviewer_actor: synthetic-clawsweeper
review_status: complete
review_terminal_failure: false
decision: keep_open
process_gates: ["owner_merge_authority"]
maintainer_decision: {"required":false}
triage_priority: P3
merge_risk_labels: ["merge-risk: 🚨 automation"]
labels: ["do-not-copy-this-snapshot"]
label_justifications: [{"label":"proof: override","reason":"not publication authority"}]
real_behavior_proof_status: sufficient
real_behavior_proof_evidence_kind: recording
real_behavior_proof_needs_contributor_action: false
pr_rating_overall: B
pr_rating_proof: A
pr_rating_patch: B
---

# Synthetic native review fixture — not a real review verdict

## Summary

The synthetic adapter preserves native presentation without changing runtime authority.

## What This Changes

Carries accepted native findings, evidence and scores into the existing Conductor comment.
Label reconciliation changes only explicitly supported native families.

## System Context

The accepted artifact crosses the digest boundary once. The runtime remains the sole publisher.

## Architecture Diagram

flowchart LR
  Artifact[Accepted native artifact] --> Bound[Exact tuple and digest]
  Bound --> Comment[Owned rich comment]
  Bound --> Labels[Scoped native labels]
  Human[Human owner] --> Merge[Separate merge decision]

## Review Findings

None. This synthetic clean fixture does not assert any deployed behavior.

## Security Review

No synthetic security findings. The report cannot request secrets, merge authority or arbitrary labels.

## Real Behavior Proof

Status: sufficient

Evidence kind: recording

The synthetic recording demonstrates a repeated publication tick updating no duplicate comment.

## PR Rating

Overall tier: B

Proof tier: A

Patch tier: B

The report assigns good normal readiness and stronger proof confidence.

Next rank-up steps:

- Add another user-facing example after the prerequisite stack lands.

## Live Proof

Fixture only: no live GitHub rendering, runtime activation or device capture claimed.

## Evidence

- Synthetic repeated-tick output: one owned comment and unchanged exact labels.
- Synthetic negative case: tampered artifact rejected before publication.
- Original native report remains in the accepted workflow artifact.

## Best Possible Solution

Keep Conductor status independent; preserve the original report digest and one publication owner.

## Maintainer Decision

No native question supplied. Merge remains a separate human-only action.

## Risks / Open Questions

GitHub Mermaid rendering and production activation are intentionally unverified.

## Close Comment

INTERNAL_CLOSE_COMMENT_MUST_NOT_RENDER

## GitHub Snapshot

INTERNAL_SNAPSHOT_MUST_NOT_RENDER

## Review Telemetry

INTERNAL_TELEMETRY_MUST_NOT_RENDER
