# Issue #687 PR #18 frozen post-cap reviewer-binding repair — 2026-09-18

## Ownership

- Repository: `saari-co/review-conductor`.
- Isolated worktree:
  `/Users/cp-1/.openclaw/worktrees/0dbf576623060113/review-conductor-pr18-correctness-repair-20260918`
- Branch: `openclaw/review-conductor-issue-687-routing-v1`
- Frozen integration base `origin/main`:
  `b16e0c8bc3f32e997db31caa173b846b0e9041ce`
- Verified starting core head: `b08924779810b490573f619e5b1afa64839fc9be`
- Historical malformed-token packet:
  `proof/issue-687-routing-core-post-cap-repair-3-20260918/` remains the
  explanation of the persisted `rail='spark'` / non-string state repair
  at `b089247`. This packet does not rewrite that receipt or embed a
  later commit's own SHA.
- Historical ready-tuple packet:
  `proof/issue-687-routing-core-post-cap-repair-2-20260918/` remains the
  explanation of the impossible-ready and early-disposition repair at
  `7ee4184`.
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

On starting head `b08924779810b490573f619e5b1afa64839fc9be`, one
deterministic trusted-enrollment invariant was still false:

1. `trusted_enrollment_from_registry` returned
   `review_conductor=present` when a matching registry enrollment
   existed but `core_config` had no valid `review_policy` / reviewer
   mapping. Missing policy, a non-dict policy, a non-dict or omitted
   core config, `enabled` other than `True`, and an exact reviewer
   mismatch must fail closed. This matches
   `require_profile_enrolled()`, which requires a valid core
   `review_policy`, `enabled=True`, and the exact registry reviewer
   identities before granting authority.

The three earlier post-cap invariants remain negative and are not
reopened:

2. Persisted `rail='spark'` / non-string state still fail-close
   through the row adapter; direct public inputs remain strict.
3. `ready_for_human_merge` with `required_fix` / `human_gate`
   dispositions still fail-closes before unenrolled or legacy-only
   short-circuits.
4. `ready_for_human_merge` with `clawsweeper_result=failed` or
   `human_gate` still fail-closes to blocked instead of silent
   `merge_ready_suppressed`.

Direct reproduction of the starting missing-policy `present` grant is
now negative: those malformed cores return the existing broken pair.
Unmatched enrollment still returns the existing absent/legacy result.
Reordered exact reviewer identities still bind as present.

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
- Historical first post-cap, ready-tuple, malformed-token, and
  merge-forward proof explanations.

## Local commands and results

All commands ran in this worktree against the repaired candidate source.
Fixture PASS is not a deployed review PASS.

| Command | Result |
| --- | --- |
| direct missing/non-dict `review_policy` or `core_config` reproduction | now `review_conductor=broken` / `legacy_xapi=broken` |
| exact reviewer mismatch / `enabled` not True | now broken |
| retained unmatched / legacy-only missing policy | still absent / legacy present |
| retained exact reordered reviewer identities | still present |
| `python3 tests/test_orchestration_outcome.py` | 39 tests OK |
| focused `test_trusted_enrollment_requires_bound_core_review_policy` | PASS |
| `python3 -m unittest -q test_service_runtime` | 81 tests OK, including the new reviewer-binding negative and the precise mutant that restores the old optional-policy grant |
| `python3 -m compileall -q tools tests scripts` | passed |
| worktree/index `git diff --check` | clean |
| `python3 scripts/check_provenance.py` | `extraction hashes verified (14 files); Python compilation passed` |
| `make check` | passed: hygiene/CODEOWNERS; conductor/activation custom suites; rereview; userland `(22)`; orchestration `(39)`; cold hydration `(24)`; projection `(25)`; presentation `30`; original-report `31`; profiles `31`; scaffold `6`; trusted admission `23`; service runtime `81`; guard `5`; launcher transport `10`; suite launcher `20`; standalone supervisor `36`; runtime lifecycle `30`; workflow contract `5`; provenance `14 files`; `py_compile`; `git diff --check`. No checks skipped. |
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
  `proof/issue-687-routing-core-post-cap-repair-3-20260918/`,
  `proof/issue-687-routing-core-post-cap-repair-2-20260918/`,
  `proof/issue-687-routing-core-post-cap-repair-20260918/`, and
  `proof/issue-687-routing-core-merge-forward-20260918/` are preserved.
- No PR readiness, review request, comment resolution, or PR merge.
- No credentials, live databases, checkouts, or proof stores outside
  this Git-only packet.
- No workflow, protection, deployment, producer, Suite, registry, or
  x-api change.
- No activation follows from this source repair.
