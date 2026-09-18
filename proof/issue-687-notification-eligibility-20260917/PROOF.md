# Issue #687 Copilot 5243575441 bounded repair — 2026-09-17

## Ownership

- Repository: `saari-co/review-conductor`.
- Isolated worktree:
  `/Users/cp-1/.openclaw/worktrees/0dbf576623060113/review-conductor-issue-687-routing-v1`
- Branch: `openclaw/review-conductor-issue-687-routing-v1`
- Required start/base: `8cc4094e88c8cd04c5b7da28e0c6a9ef054ea69c` (`origin/main`).
- Authorized frozen starting HEAD / parent candidate:
  `8f03e512b57a925e270ea7b14d495002555c9294`.
- Live retained branch/PR head this repair starts from:
  `8f03e512b57a925e270ea7b14d495002555c9294`.
- Sole source-changing owner: this worktree. No x-api, Suite, or other
  repository was edited.
- Mode: one bounded repair pass for the three verified blockers in
  Copilot review `5243575441`. No Copilot, OpenClaw, or ClawSweeper
  review is requested. No merge, deploy, activation, credential,
  protection, live notification, or target-repository onboarding.

A commit cannot contain its own SHA. `candidate-manifest.json` records the
required start SHA, the parent candidate SHA, and per-file source hashes. The
branch head after this commit is the exact candidate SHA for later CI.

## Historical evidence preserved

Prior PR #18 batches remain part of the review ledger, including the
reservation-specific `BEGIN IMMEDIATE` send-lock proof at
`8f03e512b57a925e270ea7b14d495002555c9294`. The confirmed saturating 2/2
automatic-repair ledger, post-cap `required_fix` contract, and prior
adjudications are unchanged. Copilot 5242972219's cycle-2 "unbounded
repairs" finding remains rejected.

## Verified blockers repaired

1. Public orchestration enum-like inputs now require exact builtin
   strings before every membership check in `_status`, `_rail_result`,
   `_dispositions`, and the state/rail path. Malformed or unhashable
   values, including list/dict JSON and hashable str subclasses, raise
   `OrchestrationError` instead of leaking `TypeError`. Strict valid
   input behavior is unchanged.

2. Persisted readiness/quality flags accept only stored integer `0`/`1`.
   Strings such as `"false"`, other integers, floats, bools, and
   containers map to unknown rail results so the decision fails closed
   and cannot produce `merge_ready`. Public direct-input `"false"` still
   raises `OrchestrationError`; that path stays distinct from the
   persisted-row adapter.

3. Notification send eligibility revalidates the current trusted
   enrollment/route at the reserved final claim/send boundary. A
   registry or config route change between service admission/snapshot
   and delivery retires the stale notification rather than sending
   obsolete blocked/ready copy. Identical current decisions still send
   exactly once. `run_service_tick` keeps a route-freshness guard on
   every live route (re-resolve and compare the trusted pair) and adds
   exact binding checks only for the Conductor route. The existing
   `BEGIN IMMEDIATE` reservation proof is unchanged.

## Bounded refusals

- No x-api checkout, SHA pin, transport, or legacy-path change.
- No merge, deploy, credential, protection, live send, or enrollment of a
  target repository.
- No automatic merge and no notification during silent repair rounds.
- No second notification system.
- No Copilot, OpenClaw, or ClawSweeper review is requested from this
  packet.

## Local commands and results

All commands ran in this worktree against the candidate source. Fixture PASS
is not a deployed review PASS.

| Command | Result |
| --- | --- |
| focused unhashable public-input regressions | PASS; list/dict/subclass tokens raise `OrchestrationError`, not `TypeError` |
| focused persisted quality 0/1 and malformed-string regressions | PASS; `"false"`/other non-0/1 values map to unknown rails and blocked, never `merge_ready` |
| focused persisted-row/queue quality regressions | PASS; queued `"false"` sends blocked unknown-rail copy; `1` can be merge-ready; `0` stays silent |
| focused reserved enrollment-route-change retire | PASS; route change after the unlocked predicate retires and does not send; identical current decisions still send once |
| focused service registry-route-change retire | PASS; broken-to-conductor change at the reserved boundary retires stale fail-closed copy |
| focused orchestration mutants | PASS; membership-without-type and `bool()` quality mutants die |
| focused service mutants | PASS; snapshot reuse, ignored resolver, cleared freshness guard, and retained stale Conductor guard die |
| focused reservation-specific race | PASS; `BEGIN IMMEDIATE` still blocks a competing state write through transport |
| `python3 tests/test_orchestration_outcome.py` | 34 tests OK |
| `python3 tests/test_review_conductor_userland.py` | 40 passed |
| `python3 -m compileall -q tools tests scripts` | recorded after the candidate source is complete |
| `make check` | recorded after the candidate source is complete |
| `make build` | recorded after the candidate source is complete |
| `python3 scripts/check_whitespace.py 8cc4094e88c8cd04c5b7da28e0c6a9ef054ea69c <new-head>` | recorded after the candidate commit |

Exact-head whitespace is `scripts/check_whitespace.py` against
`8cc4094e88c8cd04c5b7da28e0c6a9ef054ea69c` and the post-commit HEAD. That
script requires the checkout to equal HEAD, so it runs after the candidate
commit.

Provenance destination hashes were re-verified for the 14 recorded
extracted files. This repair does not claim an x-api source branch or
commit beyond that existing ledger.

## Untouched boundaries

- No credentials, live databases, checkouts, or proof stores outside this
  Git-only packet.
- No x-api, spark-dgx, ClawSweeper, OpenClaw, or target-repository source.
- No Copilot, OpenClaw, or ClawSweeper review is requested from this
  packet. Hosted CI remains later exact-head work. This PR stays
  draft/open and does not merge or activate.

## Remaining issue

Exact-head hosted CI for the new SHA. CI green is not external review
clearance. No merge, deploy, live notification, or additional review
request is made from this packet.
