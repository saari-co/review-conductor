# Issue #687 notification delivery adapter — 2026-09-18

## Ownership

- Repository: `saari-co/review-conductor`.
- Isolated worktree:
  `/Users/cp-1/.openclaw/worktrees/0dbf576623060113/review-conductor-issue-687-routing-v1`
- Stacked branch: `openclaw/review-conductor-issue-687-notification-adapter-v1`
- Base for this slice: reduced PR #18 head
  `6497a3266feffe2d81b10d57015d736e92dd9b5a`
  (`openclaw/review-conductor-issue-687-routing-v1`).
- Preservation branch, exact full pre-split candidate:
  `openclaw/review-conductor-pr18-full-presplit-20260918` at
  `6eaf99ef11180ea523db3229c2e5c49c736bdd64`.
- Required start/base of the core PR: `8cc4094e88c8cd04c5b7da28e0c6a9ef054ea69c`
  (`origin/main`).
- Sole source-changing owner: this Cursor ACP worktree. No x-api, Suite, or
  other repository was edited.
- Mode: stacked adapter restore plus Copilot `5243987118` repairs. No Copilot,
  OpenClaw, or ClawSweeper review is requested. No merge, deploy, activation,
  credential, protection, live notification, or target-repository onboarding.

A commit cannot contain its own SHA. `candidate-manifest.json` records the
reduced-core parent SHA and per-file source hashes. The branch head after this
commit is the exact adapter SHA for later CI.

## Dependency and ledger

This slice depends on reduced PR #18. The automatic review ledger remains
exhausted at 2/2; splitting and this stacked restore do not reset it. Historical
PR #18 reviews and hosted runs stay historical, including Copilot 5243850325 at
`6eaf99ef11180ea523db3229c2e5c49c736bdd64` and
`proof/issue-687-notification-eligibility-20260917/`.

## Restored from the preserved full candidate

- Fresh trusted registry/route authority immediately before every send.
- Exact tuple/epoch fencing and no stale-route sends.
- Send leases, reserved claim/send fencing, and route-freshness guards used
  only for delivery.
- Pending-row revalidation and no duplicate delivery after uncertain outcomes.
- No external side effect was added to the reduced routing/admission core.

## Copilot 5243987118 repairs

1. Malformed persisted rail/state rows produce one concise fail-closed blocked
   notification instead of aborting the queue. Direct public inputs stay
   strict.
2. Notification event identity includes complete canonical schema, kind,
   channels, and decision identity so a retired key cannot suppress the
   current notification.
3. Retry-attempt changes revalidate the complete current event key so attempt 1
   and attempt 2 cannot both send.

## Local commands and results

All commands ran in this worktree against the adapter candidate source.
Fixture PASS is not a deployed review PASS.

| Command | Result |
| --- | --- |
| `python3 tests/test_orchestration_outcome.py` | 35 tests OK |
| `python3 tests/test_review_conductor_userland.py` | 43 passed |
| `python3 tests/test_service_runtime.py` | 93 tests OK, including adapter mutants |
| focused Copilot 5243987118 regressions | PASS: spark-rail blocked once; retired incomplete keys cannot suppress current; attempt 1 retires when attempt 2 is current |
| `python3 -m compileall -q tools tests scripts` | recorded after the candidate source is complete |
| `make check` | recorded after the candidate source is complete |
| `make build` | recorded after the candidate source is complete |
| `python3 scripts/check_whitespace.py 8cc4094e88c8cd04c5b7da28e0c6a9ef054ea69c <new-head>` | recorded after the candidate commit |

Exact-head whitespace is `scripts/check_whitespace.py` against
`8cc4094e88c8cd04c5b7da28e0c6a9ef054ea69c` and the post-commit HEAD.

## Untouched boundaries

- No credentials, live databases, checkouts, or proof stores outside this
  Git-only packet.
- No x-api, spark-dgx, ClawSweeper, OpenClaw, or target-repository source.
- No Copilot, OpenClaw, or ClawSweeper review is requested from this
  packet. This PR stays draft/open, stacked on the reduced PR #18 branch,
  and does not merge or activate.

## Remaining issue

Exact-head hosted CI for the new adapter SHA. CI green is not external review
clearance. No merge, deploy, live notification, or additional review request
is made from this packet.
