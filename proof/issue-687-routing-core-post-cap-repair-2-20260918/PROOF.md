# Issue #687 PR #18 frozen post-cap ready-tuple repair — 2026-09-18

## Ownership

- Repository: `saari-co/review-conductor`.
- Isolated worktree:
  `/Users/cp-1/.openclaw/worktrees/0dbf576623060113/review-conductor-issue-687-routing-v1`
- Branch: `openclaw/review-conductor-issue-687-routing-v1`
- Frozen integration base `origin/main`:
  `b16e0c8bc3f32e997db31caa173b846b0e9041ce`
- Verified starting core head: `009279c80314886f0bee6f314024e6e6e9a14769`
- Historical first post-cap packet:
  `proof/issue-687-routing-core-post-cap-repair-20260918/` remains the
  explanation of the draft-OpenClaw and ready-disposition repair at
  `009279c`. This packet does not rewrite that receipt or embed a later
  commit's own SHA.
- Historical merge-forward packet:
  `proof/issue-687-routing-core-merge-forward-20260918/` remains the
  explanation of the ordinary merge of exact main `b16e0c8`.
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

On starting head `009279c80314886f0bee6f314024e6e6e9a14769`, two
deterministic outcome invariants were false:

1. `ready_for_human_merge` with `clawsweeper_result=failed` or
   `human_gate` (including `human_gate=false`) returned silent
   `merge_ready_suppressed`. Those results are engine-owned
   `clawsweeper_failed` / `waiting_human` terminals, so the complete
   tuple must fail closed to the canonical blocked notification.
   Explicit `human_gate=true` on an otherwise ready tuple already
   notified blocked; the incoherent false/tuple cases now do too.
2. `ready_for_human_merge` with `required_fix` or `human_gate`
   dispositions returned `notification.eligibility=none` when
   enrollment was legacy-only or unenrolled, because the
   none/legacy short-circuit ran before disposition validation.
   Contradictory blocking dispositions must be validated before
   those route short-circuits and must produce the blocked
   fail-closed path.

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
- Historical first post-cap and merge-forward proof explanations.

## Local commands and results

All commands ran in this worktree against the repaired candidate source.
Fixture PASS is not a deployed review PASS.

| Command | Result |
| --- | --- |
| direct ready+`clawsweeper_result=failed` reproduction | now `fail_closed` / `blocked` / `inconsistent_state_rail_result_fail_closed` |
| direct ready+`clawsweeper_result=human_gate` with `human_gate=false` | now `fail_closed` / `blocked` / `inconsistent_state_rail_result_fail_closed` |
| direct unenrolled/legacy ready+`required_fix`/`human_gate` dispositions | now `fail_closed` / `blocked` / `inconsistent_ready_disposition_fail_closed` |
| `python3 tests/test_orchestration_outcome.py` | 38 tests OK, including both new negatives and 35 precise mutants |
| `python3 -m compileall -q tools tests scripts` | passed |
| worktree/index `git diff --check` | clean |
| `python3 scripts/check_provenance.py` | `extraction hashes verified (14 files); Python compilation passed` |
| `make check` | passed: hygiene/CODEOWNERS; conductor/activation custom suites; rereview; userland `(22)`; orchestration `(38)`; cold hydration `(24)`; projection `(25)`; presentation `30`; original-report `31`; profiles `31`; scaffold `6`; trusted admission `23`; service runtime `80`; guard `5`; launcher transport `10`; suite launcher `20`; standalone supervisor `36`; runtime lifecycle `30`; workflow contract `5`; provenance `14 files`; `py_compile`; `git diff --check`. No checks skipped. |
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
- Historical packets
  `proof/issue-687-routing-core-post-cap-repair-20260918/` and
  `proof/issue-687-routing-core-merge-forward-20260918/` are preserved.
- No PR readiness, review request, comment resolution, or PR merge.
- No credentials, live databases, checkouts, or proof stores outside
  this Git-only packet.
- No workflow, protection, deployment, producer, Suite, registry, or
  x-api change.
- No activation follows from this source repair.
