# Scheduling policy v2 proof

- Base: `c6df9bb1368766253fed35c243585142ffd5b392`
- Branch: `openclaw/review-conductor-pr5-scheduling`
- Captured: 2026-09-13 America/New_York
- Activation: not performed

## Qualified behavior

- Every accepted exact draft or ready head runs CI then OpenClaw without a fixed quiet period.
- A clean OpenClaw result on a draft head waits without dispatching ClawSweeper.
- `ready_for_review` dispatches ClawSweeper for the already-cleared exact revision without changing its review epoch.
- Returning to draft obsoletes a not-yet-dispatched ClawSweeper action while preserving exact-head CI/OpenClaw clearance.
- Returning to ready requeues only the exact ClawSweeper action obsoleted by that draft transition.
- A fully cleared head that returns to draft enters a distinct non-ready state; returning ready restores its consumed exact-tuple evidence without redispatch.
- A submitted or uncertain ClawSweeper dispatch remains in-flight across a draft transition and can still deliver terminal evidence; only pre-transport work is paused.
- Draft/ready timestamps do not invalidate an already-started CI run for the same head incarnation.
- Clean adjudication while draft cannot dispatch or clear ClawSweeper.
- New base/head tuples supersede prior work; duplicate events remain idempotent.
- Closed heads receive cleanup-only projection, including when no projection row previously existed, remove any remote ready label, honor a requested PR filter, and never cold-create rail checks.
- The service and maintenance CLI share an exclusive state-root tenant operation lock independent of registry copies, use the same loaded configuration that selected that lock, and prevent notification reconciliation while the sender is active.

## Verification

- `make check`: PASS
- `make build`: PASS
- `python3 -m py_compile tools/*.py`: PASS
- `git diff --check`: PASS
- Provenance verification: PASS

Copilot's reviews on `69b972d7c3ea95dc2f8758f430154dab9d4b8d50`,
`f7c98462eed3747465012f582299eb46ed846a76`, and
`29de296ab49330b92b3f7a075eaeb713ec35377b` identified the repaired
state-transition, action-resume, adjudication, closed-projection, schema,
operation-locking, and operator-documentation defects. Direct regressions and eleven
executable mutants cover the behavioral fixes. Those reviews are repair input, not
clearance for the follow-up head.

## Limits

This is source qualification only. No registry, credentials, service, HTTPS endpoint, webhook, reviewer producer, check cutover, deployment, or merge was activated. The target repositories still require a separately reviewed v2 manifest promotion before this scheduler can admit them.
