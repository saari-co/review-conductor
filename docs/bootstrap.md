# Bootstrap and cutover gates

## Current stop line

PR #1 is an undeployed draft foundation. `main` contains only the README bootstrap
commit `c01a44382bfa66c0e3be39905dd2b9f47c6c7333`. No prior CODEOWNERS exists on the
base. A new CODEOWNERS on a PR does not protect that PR: GitHub uses the base
branch's file. The repo is not self-enrolled and has no independent exact-tuple
OpenClaw/ClawSweeper clearance. CI and author-written policy are not that clearance.
No bootstrap exception, adjudication, readiness change or merge is recorded here.

## Human bootstrap decision (pending)

Before any readiness/merge change, both designated owners must examine the exact
base/head, full diff, security findings, hosted CI issuer/results and missing
external rails, and explicitly decide whether this scaffold-only bootstrap is
acceptable. Verify their GitHub IDs/access against the governance map. Record
actual authenticated decisions, not an agent's inference or checked template box.
An exception, if humans choose one, must name repository/PR/base/head, missing
checks, reason, evidence, accepted risk, scope and expiry; it is never PASS and
expires on tuple change. None is created by this document or this work.

[Main protection](branch-protection.md) is now applied and its configuration was
read back without mutation. That protection does not retroactively add CODEOWNERS
to this PR's base or satisfy the pending bootstrap decision. Because base
CODEOWNERS is absent, bootstrap must explicitly cover that enforcement gap. Do not
weaken or bypass protection to merge this PR; any exceptional bootstrap procedure
still needs its own concrete human decision. Every later settings change requires
separate owner authorization, read-back and approved safe verification.

A bootstrap merge would accept source only, **not** credentials, enrollment,
service deployment, authoritative checks or migration. Keep those grants separate.

## Ordered work after scaffold acceptance

1. Implement trusted enrollment and approved base-policy loader/hash binding.
   Fail closed on missing identities, mismatch, or PR-head policy self-promotion.
2. Implement authenticated service/client API and typed event outbox. Keep
   notification sends singly owned; no direct Gateway/Smoky state access.
3. Qualify webhook/check publishing, OpenClaw comprehensive exact-tuple review,
   and ClawSweeper exact-tuple dispatch/artifacts with stale/cross-target tests.
4. With separate authorization, deploy isolated shadow mode: per-target
   credentials/state and non-authoritative canary check names. Validate rollback,
   exact source, genuine evidence and health. Never dual authoritative writers.
5. Separately commit SMCBD's manifest, remove/rename Actions placeholder rail jobs,
   observe genuine App-owned checks, then seek owner authorization for a precise
   protection rebinding. PR #3 is not changed by scaffold work.
6. Restart quiet period -> deterministic CI -> OpenClaw -> ClawSweeper at a new
   exact head. No stale or skipped evidence is carried forward as PASS.
7. Only after SMCBD proves the standalone path and rollback, migrate Blocks.
   Retire x-api ownership in a separate reviewed and authorized change.

Do not mutate x-api, Gateway, Smoky, the live conductor or either target as a side
effect of any source-only scaffold change. Each activation/cutover proposal must
specify the target, source SHA, check issuer/names, isolation, writer fencing,
rollback and remaining blockers before human authorization.
