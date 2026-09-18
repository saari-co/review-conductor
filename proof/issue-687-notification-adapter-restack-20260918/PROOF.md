# Issue #687 notification adapter restack — 2026-09-18

## Ownership

- Repository: `saari-co/review-conductor`.
- Isolated worktree:
  `/Users/cp-1/.openclaw/worktrees/0dbf576623060113/review-conductor-issue-687-routing-v1`
- Stacked branch: `openclaw/review-conductor-issue-687-notification-adapter-v1`
- Corrected PR #18 core now merged as an ancestor:
  `04b2f583c7cf9a6a62dd0428b2497fb55087c754`
  (`openclaw/review-conductor-issue-687-routing-v1`).
- Previous adapter head whose runtime behavior is preserved:
  `65b10aff8faa78550f899881505b286b9116caae`.
- Preservation branch, exact full pre-split candidate:
  `openclaw/review-conductor-pr18-full-presplit-20260918` at
  `6eaf99ef11180ea523db3229c2e5c49c736bdd64`.
- Required start/base of the core PR: `8cc4094e88c8cd04c5b7da28e0c6a9ef054ea69c`
  (`origin/main`).
- Sole source-changing owner: this Cursor ACP worktree. No x-api, Suite, or
  other repository was edited.
- Mode: ordinary merge of the corrected core plus truthful proof restack.
  Runtime integration stays the `65b10aff` restore, including Copilot
  `5243987118` repairs. No Copilot, OpenClaw, or ClawSweeper review is
  requested. No merge, deploy, activation, credential, protection, live
  notification, or target-repository onboarding.

A commit cannot contain its own SHA. `candidate-manifest.json` records the
corrected-core parent SHA and per-file source hashes for the adapter slice
relative to that core. The branch head after the proof commit is the exact
adapter SHA for later CI.

Historical packet `proof/issue-687-notification-adapter-20260918/` remains
exact-head truthful for `65b10aff8faa78550f899881505b286b9116caae`.

## Dependency and ledger

This slice depends on corrected PR #18. The automatic review ledger remains
exhausted at 2/2; splitting, core correction, and this restack do not reset
it. Historical PR #18 reviews and hosted runs stay historical, including
Copilot 5243850325 at `6eaf99ef11180ea523db3229c2e5c49c736bdd64` and
`proof/issue-687-notification-eligibility-20260917/`.

## Restored relative to the corrected core

Because the corrected core no longer changes live runtime entry functions,
`git diff` core..adapter now contains the full runtime integration:

- Userland `run_tick` route suppression.
- `notification_message`, `notification_event_identity`,
  `queue_notifications`, and `deliver_notifications`.
- Service tick wiring, `service_entrypoint.registry_provider` document-only
  loading, and registry/send guards plus the send lease.
- Fresh trusted registry/route authority immediately before every send.
- Exact tuple/epoch fencing and no stale-route sends.
- Pending-row revalidation and no duplicate delivery after uncertain outcomes.

## Copilot 5243987118 repairs

1. Malformed persisted rail/state rows produce one concise fail-closed blocked
   notification instead of aborting the queue. Direct public inputs stay
   strict.
2. Notification event identity includes complete canonical schema, kind,
   channels, and decision identity so a retired key cannot suppress the
   current notification.
3. Retry-attempt changes revalidate the complete current event key so attempt 1
   and attempt 2 cannot both send.

Final behavior matches `65b10aff8faa78550f899881505b286b9116caae` except the
boundary relocation onto the corrected core tip.

## Local commands and results

All commands ran in this worktree against the restacked adapter source.
Fixture PASS is not a deployed review PASS.

| Command | Result |
| --- | --- |
| `python3 tests/test_orchestration_outcome.py` | 35 tests OK |
| `python3 tests/test_review_conductor_userland.py` | 43 passed |
| focused Copilot 5243987118 regressions | PASS: spark-rail blocked once; retired incomplete keys cannot suppress current; attempt 1 retires when attempt 2 is current |
| `AdmissionIngressTests.test_service_tick_wires_registry_owned_enrollment_routes` | ok |
| `python3 -m compileall -q tools tests scripts` | passed |
| `make check` | passed |
| `make build` | passed; wrote `dist/review-conductor.pyz` (untracked, not committed) |
| `python3 scripts/check_whitespace.py` against `origin/main` and against the corrected core | recorded after the candidate commit |

Exact-head whitespace is `scripts/check_whitespace.py` against
`8cc4094e88c8cd04c5b7da28e0c6a9ef054ea69c` and the post-commit HEAD. The
same script is also run against the corrected core tip
`04b2f583c7cf9a6a62dd0428b2497fb55087c754`.

## Untouched boundaries

- No credentials, live databases, checkouts, or proof stores outside this
  Git-only packet.
- No x-api, spark-dgx, ClawSweeper, OpenClaw, or target-repository source.
- No Copilot, OpenClaw, or ClawSweeper review is requested from this
  packet. This PR stays draft/open, stacked on the corrected PR #18 branch,
  and does not merge or activate.

## Remaining issue

Exact-head hosted CI for the new adapter SHA and for the corrected core SHA.
CI green is not external review clearance. No merge, deploy, live
notification, or additional review request is made from this packet.
