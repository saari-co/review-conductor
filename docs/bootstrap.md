# Bootstrap and cutover gates

## PR #1 bootstrap boundary

PR #1's base is the README-only bootstrap commit
`c01a44382bfa66c0e3be39905dd2b9f47c6c7333`. No prior CODEOWNERS exists on that
base. A new CODEOWNERS on a PR does not protect that PR: GitHub uses the base
branch's file. The repo is not self-enrolled and has no independent exact-tuple
OpenClaw/ClawSweeper clearance. CI and author-written policy are not that clearance.

## Human bootstrap decision

Before readiness/merge, both designated owners must examine the exact
base/head, full diff, security findings, hosted CI issuer/results and missing
external rails, and explicitly decide whether this scaffold-only bootstrap is
acceptable. Verify their GitHub IDs/access against the governance map. Record
actual authenticated decisions, not an agent's inference or checked template box.
The resulting exception must be recorded by an authenticated owner in PR #1's
GitHub conversation; this source file cannot self-authorize its own merge. It
must name repository/PR/base/head, missing
checks, reason, evidence, accepted risk, scope and expiry; it is never PASS and
expires on tuple change.

[Main protection](branch-protection.md) is now applied and its configuration was
read back without mutation. That protection does not retroactively add CODEOWNERS
to this PR's base or satisfy the pending bootstrap decision. Because base
CODEOWNERS is absent, bootstrap must explicitly cover that enforcement gap. A
mapped owner's trusted-owner bypass may be used only after the exact-tuple
exception is recorded, required CI passes and the other owner has supplied an
exact-head decision. That bypass is an auditable owner override, not review PASS.
Every later settings change requires separate owner authorization, read-back and
approved safe verification.

A bootstrap merge would accept source only, **not** credentials, enrollment,
service deployment, authoritative checks or migration. Keep those grants separate.

## Ordered work after scaffold acceptance

1. Trusted enrollment and approved base-policy loader/hash binding: implemented
   as an inert library plus authenticated source integration. A live external
   registry, target approved-policy commit and credentials remain unprovisioned.
2. Implement authenticated service/client API and typed event outbox. Keep
   notification sends singly owned; no direct Gateway/Smoky state access.
3. Webhook/check/policy adapters now have isolated source qualification. Still
   required: live shadow qualification of OpenClaw comprehensive exact-tuple
   review and ClawSweeper dispatch/artifacts with genuine App-owned checks.
4. With separate authorization, deploy isolated shadow mode: per-target
   credentials/state and non-authoritative canary check names. Validate rollback,
   exact source, genuine evidence and health. Never dual authoritative writers.
5. Separately commit SMCBD's manifest, remove/rename Actions placeholder rail jobs,
   observe genuine App-owned checks, then seek owner authorization for a precise
   protection rebinding. PR #3 is not changed by scaffold work.
6. Restart deterministic CI -> OpenClaw at every new exact head; add ClawSweeper
   only while that exact head is ready. No stale or skipped evidence is carried forward as PASS.
7. Only after SMCBD proves the standalone path and rollback, migrate Blocks.
   Retire x-api ownership in a separate reviewed and authorized change.

Do not mutate x-api, Gateway, Smoky, the live conductor or either target as a side
effect of any source-only scaffold change. Each activation/cutover proposal must
specify the target, source SHA, check issuer/names, isolation, writer fencing,
rollback and remaining blockers before human authorization.
