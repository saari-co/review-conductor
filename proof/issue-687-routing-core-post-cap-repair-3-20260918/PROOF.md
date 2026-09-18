# Issue #687 PR #18 frozen post-cap malformed-token repair — 2026-09-18

## Ownership

- Repository: `saari-co/review-conductor`.
- Isolated worktree:
  `/Users/cp-1/.openclaw/worktrees/0dbf576623060113/review-conductor-pr18-correctness-repair-20260918`
- Branch: `openclaw/review-conductor-issue-687-routing-v1`
- Frozen integration base `origin/main`:
  `b16e0c8bc3f32e997db31caa173b846b0e9041ce`
- Verified starting core head: `7ee4184b5bb6cbf4082055d08cdb0105c2688fb4`
- Historical ready-tuple packet:
  `proof/issue-687-routing-core-post-cap-repair-2-20260918/` remains the
  explanation of the impossible-ready and early-disposition repair at
  `7ee4184`. This packet does not rewrite that receipt or embed a later
  commit's own SHA.
- Historical first post-cap packet:
  `proof/issue-687-routing-core-post-cap-repair-20260918/` remains the
  explanation of the draft-OpenClaw and ready-disposition repair at
  `009279c`.
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

On starting head `7ee4184b5bb6cbf4082055d08cdb0105c2688fb4`, one
deterministic persisted-row invariant was still false:

1. `outcome_from_review_row` mapped inconsistent stored rails to typed
   unknown results, but still forwarded malformed tokens such as
   `rail='spark'` or a non-string state. Canonical
   `decide_orchestration_outcome` then raised `OrchestrationError`
   (`rail is unknown` / `state is unknown`) instead of reaching
   fail-closed blocked handling. Direct public inputs must remain
   strict.

The two ready-tuple invariants repaired at `7ee4184` remain negative
and are not reopened:

2. `ready_for_human_merge` with `required_fix` / `human_gate`
   dispositions still fail-closes before unenrolled or legacy-only
   short-circuits.
3. `ready_for_human_merge` with `clawsweeper_result=failed` or
   `human_gate` still fail-closes to blocked instead of silent
   `merge_ready_suppressed`. Valid ready+clean/findings and human-only
   merge are unchanged.

Direct reproduction of the starting `rail='spark'` raise is now
negative: the adapter emits exact builtin state/rail values plus typed
unknown results, and decide returns `fail_closed` / `blocked` /
`unknown_rail_result_fail_closed`. Direct `rail='spark'` and
non-string state still raise.

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
- Historical first post-cap, ready-tuple, and merge-forward proof
  explanations.

## Local commands and results

All commands ran in this worktree against the repaired candidate source.
Fixture PASS is not a deployed review PASS.

| Command | Result |
| --- | --- |
| direct persisted `waiting_human` + `rail='spark'` reproduction | now `fail_closed` / `blocked` / `unknown_rail_result_fail_closed` |
| direct public `rail='spark'` / non-string state | still `OrchestrationError` |
| retained ready+`clawsweeper_result=failed`/`human_gate` | still `fail_closed` / `blocked` / `inconsistent_state_rail_result_fail_closed` |
| retained unenrolled/legacy ready+`required_fix`/`human_gate` dispositions | still `fail_closed` / `blocked` / `inconsistent_ready_disposition_fail_closed` |
| `python3 tests/test_orchestration_outcome.py` | 39 tests OK, including the new malformed-token negative and 37 precise mutants |
| `python3 -m compileall -q tools tests scripts` | passed |
| worktree/index `git diff --check` | clean |
| `python3 scripts/check_provenance.py` | `extraction hashes verified (14 files); Python compilation passed` |
| `make check` | passed: hygiene/CODEOWNERS; conductor/activation custom suites; rereview; userland `(22)`; orchestration `(39)`; cold hydration `(24)`; projection `(25)`; presentation `30`; original-report `31`; profiles `31`; scaffold `6`; trusted admission `23`; service runtime `80`; guard `5`; launcher transport `10`; suite launcher `20`; standalone supervisor `36`; runtime lifecycle `30`; workflow contract `5`; provenance `14 files`; `py_compile`; `git diff --check`. No checks skipped. |
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
  `proof/issue-687-routing-core-post-cap-repair-2-20260918/`,
  `proof/issue-687-routing-core-post-cap-repair-20260918/`, and
  `proof/issue-687-routing-core-merge-forward-20260918/` are preserved.
- No PR readiness, review request, comment resolution, or PR merge.
- No credentials, live databases, checkouts, or proof stores outside
  this Git-only packet.
- No workflow, protection, deployment, producer, Suite, registry, or
  x-api change.
- No activation follows from this source repair.
