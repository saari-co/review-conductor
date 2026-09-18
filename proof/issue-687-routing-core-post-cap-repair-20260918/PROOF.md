# Issue #687 PR #18 frozen post-cap routing-core repair — 2026-09-18

## Ownership

- Repository: `saari-co/review-conductor`.
- Isolated worktree:
  `/Users/cp-1/.openclaw/worktrees/0dbf576623060113/review-conductor-issue-687-routing-v1`
- Branch: `openclaw/review-conductor-issue-687-routing-v1`
- Frozen integration base `origin/main`:
  `b16e0c8bc3f32e997db31caa173b846b0e9041ce`
- Verified starting core head: `2e40ffd1c8adb3062cb5af64f7a8ccaf5cab7ee5`
- Historical merge-forward packet:
  `proof/issue-687-routing-core-merge-forward-20260918/` remains the
  explanation of the ordinary merge of exact main `b16e0c8` into the
  independently mergeable routing/admission core. This packet does not
  rewrite that merge or embed a later receipt commit's own SHA.
- Sole source-changing owner: this Cursor ACP worktree. No x-api, Suite,
  or adapter branch was edited.
- Mode: owner-authorized frozen post-cap repair of draft PR #18. The
  review ledger remains exhausted at 2/2 and is not reset. No rebase,
  amend, force-push, history rewrite, Copilot, OpenClaw, or ClawSweeper
  review request. No merge of PR #18, deploy, activation, credential,
  protection, live notification, readiness flip, or target-repository
  onboarding.

A commit cannot contain its own SHA. `candidate-manifest.json` records
the frozen main base, the verified starting core head, and per-file
source hashes. The branch head after this ordinary commit is the exact
candidate SHA for hosted CI.

## Why this repair exists

On starting head `2e40ffd1c8adb3062cb5af64f7a8ccaf5cab7ee5`, two
deterministic outcome invariants were false:

1. `openclaw_clean_draft` with `openclaw_result=clean` returned
   `clawsweeper_eligible=true`. A clean draft must keep ClawSweeper
   ineligible.
2. `ready_for_human_merge` with `ready_qualified=true` and
   `adjudication_dispositions` containing `required_fix` or
   `human_gate` returned `notification.eligibility=merge_ready`. Only
   no dispositions or the allowed `defer` / `reject_false_positive`
   set may be compatible with readiness. Contradictory
   state/disposition combinations must use canonical fail-closed
   blocked handling instead of becoming merge-ready or silently
   clearing the disposition.

Direct reproductions of those two starting results are now negative.

## Retained in repaired PR #18

- Independently mergeable routing/state/admission core on current main
  `b16e0c8`.
- Versioned `review-conductor.orchestration-outcome.v1`.
- Saturating `repair_cycle=2` ledger. This explicit post-cap
  `required_fix` packet does not reset it.
- Inert-core boundary versus current `origin/main`: no live
  `notification_message`, `notification_event_identity`,
  `queue_notifications`, `deliver_notifications`, `run_tick`,
  `run_service_tick`, `service_entrypoint.registry_provider`, send
  lease, or notifier-transport change.
- Historical merge-forward proof explanation for `2e40ffd`.

## Local commands and results

All commands ran in this worktree against the repaired candidate source.
Fixture PASS is not a deployed review PASS.

| Command | Result |
| --- | --- |
| direct `openclaw_clean_draft` reproduction | now `clawsweeper_eligible=false` |
| direct ready+`required_fix`/`human_gate` reproduction | now `fail_closed` / `blocked` / `inconsistent_ready_disposition_fail_closed` |
| `python3 tests/test_orchestration_outcome.py` | 36 tests OK, including both new negatives and precise mutants |
| `python3 -m compileall -q tools tests scripts` | passed |
| worktree/index `git diff --check` | clean |
| `python3 scripts/check_provenance.py` | `extraction hashes verified (14 files); Python compilation passed` |
| `make check` | passed: hygiene/CODEOWNERS; conductor/activation custom suites; rereview; userland `(22)`; orchestration `(36)`; cold hydration `(24)`; projection `(25)`; presentation `30`; original-report `31`; profiles `31`; scaffold `6`; trusted admission `23`; service runtime `80`; guard `5`; launcher transport `10`; suite launcher `20`; standalone supervisor `36`; runtime lifecycle `30`; workflow contract `5`; provenance `14 files`; `py_compile`; `git diff --check`. No checks skipped. |
| `make build` | passed; wrote ignored `dist/review-conductor.pyz` (4138 bytes) |

Exact-head whitespace is `scripts/check_whitespace.py` against
`b16e0c8bc3f32e997db31caa173b846b0e9041ce` and the post-commit HEAD.
That script requires the checkout to equal HEAD, so it runs after the
candidate commit.

This repair does not claim an x-api source branch or commit beyond the
existing ledger. Provenance destination hashes are unchanged because no
extracted file was edited.

## Untouched boundaries

- Adapter branch
  `openclaw/review-conductor-issue-687-notification-adapter-v1` is not
  edited in this packet.
- Merge-forward packet
  `proof/issue-687-routing-core-merge-forward-20260918/` is preserved.
- No PR readiness, review request, comment resolution, or PR merge.
- No credentials, live databases, checkouts, or proof stores outside
  this Git-only packet.
- No workflow, protection, deployment, producer, Suite, registry, or
  x-api change.
- No activation follows from this source repair.
