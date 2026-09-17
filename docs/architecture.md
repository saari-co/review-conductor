# ADR 001: Independent conductor; repository-owned requirements

Status: accepted direction from the owner on 2026-09-11; source-qualified native
publication boundary updated 2026-09-15; deployment deferred.

## Decision

Review Conductor has its own repository, runtime, build and release lifecycle.
Targets declare review requirements in a versioned `.review-conductor.json`,
similar to a repository-local reviewer configuration. One multi-tenant service
implementation reads approved configuration; it does not accumulate each
product's implementation. Initial enrollment is
`saari-co/openclaw-smcbd-suite`, followed by `dinkuskit/blocks`. Tenants may
share implementation and read-only dashboard visibility, but credentials, App
installations, enrollment, mutable state, queues, proof and mutation authority
remain isolated. Adding a later repository is explicit configuration/enrollment
work, not a new service fork.

| Owner | Responsibility |
| --- | --- |
| Target repository | CI workflow identity, default branch, readiness gate and required rails |
| Conductor service | Exact tuple/epoch state, admission, adapter sequencing, bounded repair routing, audit evidence and sole owned GitHub publication |
| Service enrollment registry | Numeric repo identity, App installation, approved policy SHA/hash, authoritative reviewer actors, credential references and isolation domains |
| GitHub Actions | Deterministic CI |
| Reviewer services | Original native reports and detailed findings as comprehensive exact-tuple evidence through qualified adapters; no competing writer for Conductor-owned publication |
| Gateway / Smoky | Narrow client requests and status explanations; no conductor database access |
| Human owner | Policy promotion, adjudication authority and merge authorization |

No shared database, source import, npm workspace, submodule, or coordinated
release with x-api/Gateway/Smoky. A conductor outage blocks review clearance,
not Gateway startup or unrelated agent work. No automatic merge endpoint.

The canonical tuple is repository + PR + base SHA + head SHA + review epoch.
Any base/head change invalidates evidence. Webhook authentication precedes
parsing; replays are idempotent; conflicting deliveries fail closed. Uncertain
non-idempotent dispatch is reconciled, not blindly retried. The first two
repair cycles are a saturating automatic-repair ledger, not a ceiling on later
scoped ``required_fix`` routes; reviewers never become mutation owners.

Enrollment and terminal notification eligibility are one Conductor-owned
decision, [`decide_orchestration_outcome`](../tools/orchestration_outcome.py)
with schema `review-conductor.orchestration-outcome.v1`. A Review
Conductor-enrolled repository uses the automatic CI → OpenClaw →
repair/adjudication → ClawSweeper pipeline. A repository enrolled only in
legacy x-api rails is reported as `route=legacy_xapi` with handoff required;
Conductor does not import, encode, or dispatch the x-api conveyor
(`legacy_dispatch` stays false). Dual enrollment selects Review
Conductor and forbids duplicate legacy dispatch. Neither enrollment ends the
process with no review and no notification. Ambiguous or broken enrollment
fails closed and is never treated as unenrolled. `run_service_tick`
resolves that pair from the authoritative registry/admission result and
passes it into `run_tick`. The v2 service-owned registry may carry an
optional exact-profile `legacy_xapi` marker; omitted documents remain
legacy absent. `trusted_enrollment_from_registry` reads only that loaded
field. Userland activation flags (`enabled` /
`blockers`) do not select legacy, none, dual, or broken routes.
Hydration, action draining, result collection, and review stages run
only when the trusted route is `review_conductor`. Dual enrollment
selects Review Conductor; legacy-only, unenrolled, and broken routes
stay real runtime behavior and never dispatch x-api. An explicit
`human_gate=true` takes precedence over merge-ready and silent
nonterminal progression: no structurally valid gated input may become
`merge_ready` or continue review dispatch silently. The existing
notification queue still consumes the current-head decision; event
identity includes canonical route, reason, and eligibility so a stale
pending row can retire while the current blocked decision enqueues and
delivers once. Pending rows are revalidated against the current head
and trusted enrollment before claim or send, and ineligible rows are
retired. Caller payloads cannot grant Conductor authority. `closed` and
`closed_merged` are terminal silent non-dispatchable states on every
direct and service-loop path, including when trusted enrollment is
broken; closed-state handling precedes enrollment short-circuits.

The existing notification queue consumes that outcome. It does not invent a
second sender. `repair_required` and `awaiting_adjudication` without a genuine
human gate are silent internal progression, including the first two automatic
repair rounds. At ledger cycle 2, `required_fix` may still create a scoped
repair route, change the head, and rerun exact-head rails while preserving
cycle 2; later findings stay in adjudication without another broad automatic
round or ledger reset. OpenClaw findings suppress ClawSweeper eligibility.
ClawSweeper findings suppress merge-ready eligibility. Merge-ready notification
requires both required exact-head rails to be effectively clean, including
authorized deferrals or rejections on an unchanged head, plus the existing
ready-quality policy, and no explicit `human_gate`. Only merge-ready or
genuinely blocked/human-action-required outcomes notify. Terminal copy is
`<repo>#<pr> ready to merge` or `<repo>#<pr> blocked — <specific reason>`,
optionally with the PR URL, and carries no transcript, progress, cycle, tier,
or proof prose. A changed head preserves the 2/2 repair ledger. Representable
fail-closed enrollment, unknown state/result, and equivalent invalid
orchestration states keep routing and dispatch suppressed and stay eligible
for the blocked notification path; the queue must not raise or silently pass
them. Fail-closed copy uses the canonical decision reason, not stale
persisted blocker text. Malformed inputs still raise. See the
[decision map](orchestration-decision-map.md) for later #687 workstreams.

The Conductor is the sole writer of the authoritative `OpenClaw Review Rail`
and `ClawSweeper Review Rail` checks after it validates reviewer-native evidence.
A completed exact-head review can project review-success while merge stays
human-only. Check output names the current stage, decision reason, exact head,
workflow run, and accepted artifact digest.

For the native publication route, reviewer services retain authority over their
original reports and findings as evidence. Conductor is the sole GitHub writer
of its one idempotent, marker-bound projection comment and explicitly owned
ClawSweeper label families. It renders selected original public sections and
safe supplied diagrams; explicit accepted native fields select allowlisted
rating, priority, proof/media and merge-risk labels separately from Conductor's
status labels. It does not regrade evidence, change status semantics or acquire
adjudication authority. Exact tuple/epoch, accepted digest and current admission
remain prerequisites for current publication; unowned labels are preserved.

This source-qualified boundary supersedes the earlier status-label-only /
reviewer-detailed-comment split. It is not activation or permission for competing
writers. The [integration contract](integration-contract.md#rich-native-clawsweeper-publication-source-qualified-activation-held)
defines the bounds and the held refresh/requalification gates. Dashboard
projections remain read-only, and merge authority remains human-only.

## Practical transition

Reuse the tested Python/SQLite implementation, not a new workflow framework.
Python standard-library unittest, zipfile and argparse provide the scaffold's
build/test tools. No new hosted platform or paid dependency is introduced.
Legacy module/schema names remain for compatibility, not architectural ownership.
Host-specific launch, Spark and notification adapters are preserved for regression
coverage but are not exposed by the packaged scaffold. Qualify replacements at
the adapter boundary before a separate live migration.
