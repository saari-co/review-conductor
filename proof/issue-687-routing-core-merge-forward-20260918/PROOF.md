# Issue #687 PR #18 routing/admission core × main merge-forward — 2026-09-18

## Ownership

- Repository: `saari-co/review-conductor`.
- Isolated worktree:
  `/Users/cp-1/.openclaw/worktrees/0dbf576623060113/review-conductor-issue-687-routing-v1`
- Branch: `openclaw/review-conductor-issue-687-routing-v1`
- Verified pre-merge core head: `9b82ea60cfd25e3ae794ea910c369d1041a29c08`
- Frozen integration base `origin/main`: `b16e0c8bc3f32e997db31caa173b846b0e9041ce`
- Historical merge-base: `8cc4094e88c8cd04c5b7da28e0c6a9ef054ea69c`
- Sole source-changing owner: this Cursor ACP worktree. No x-api, Suite, or
  adapter branch was edited.
- Mode: sequential PR #18 merge-forward only. Normal non-force merge of exact
  main into the core branch. No rebase, no force-push, no Copilot, OpenClaw,
  or ClawSweeper review request. No merge of PR #18, deploy, activation,
  credential, protection, live notification, or target-repository onboarding.

A commit cannot contain its own SHA. `candidate-manifest.json` records the
verified pre-merge core head, exact main, merge parents, and per-file source
hashes. The branch head after this merge commit is the exact candidate SHA
for hosted CI.

## Starting state verified before edit

| Binding | Exact value |
| --- | --- |
| Assigned worktree start | `bc80b1e97f75148cd1b5be71b75f8c82cb3b9fdf` on the stacked adapter branch; switched away without editing it |
| Core branch after switch | `9b82ea60cfd25e3ae794ea910c369d1041a29c08`, clean |
| Fetched `origin/main` | `b16e0c8bc3f32e997db31caa173b846b0e9041ce` |
| Worktree | clean before merge |

## Merge parents

Normal non-force merge of exact main into the PR #18 core branch:

- First parent (core head): `9b82ea60cfd25e3ae794ea910c369d1041a29c08`
- Second parent (main): `b16e0c8bc3f32e997db31caa173b846b0e9041ce`

Expected textual conflicts only:

- `docs/architecture.md` — keep the saturating 2/2 automatic-repair ledger
  and add maintainer `@ClawSweeper rereview` / `@clawsweeper re-review`.
- `docs/provenance.json` — keep the saturating-ledger adaptation, append
  the rereview adaptation, and re-pin `tools/review_conductor.py`
  destination SHA-256 to the merged bytes.

Auto-merged overlap includes `Makefile`, `docs/integration-contract.md`,
`tools/review_conductor.py`, `tools/review_conductor_runtime.py`,
`tools/review_conductor_userland.py`, `tools/service_runtime.py`, and
`tests/test_service_runtime.py`.

Required compatibility repair only: `test_exhausted_repair_budget_refuses_rereview`
now expects the third `required_fix` to stay `repair_required` at cycle 2,
then reaches `waiting_human` on a later head before refusing rereview.
No live queue, delivery, `run_tick`, or adapter-branch edit.

## Local commands and results

All commands ran in this worktree against the merge-forward candidate source.
Fixture PASS is not a deployed review PASS.

| Command | Result |
| --- | --- |
| `python3 tests/test_clawsweeper_rereview.py` | `clawsweeper rereview tests passed` |
| `python3 tests/test_review_conductor.py` | `review conductor integration tests passed` |
| `python3 tests/test_review_conductor_userland.py` | `review conductor userland tests passed (22)` |
| `python3 tests/test_orchestration_outcome.py` | 34 tests OK |
| `python3 tests/test_trusted_admission.py` | 23 tests OK |
| `python3 -m compileall -q tools tests scripts` | passed |
| `make check` | passed: hygiene/CODEOWNERS; conductor/activation custom suites; rereview; userland `(22)`; orchestration `(34)`; cold hydration `(24)`; projection `(25)`; presentation `30`; original-report `31`; profiles `31`; scaffold `6`; trusted admission `23`; service runtime `80`; guard `5`; launcher transport `10`; suite launcher `20`; standalone supervisor `36`; runtime lifecycle `30`; workflow contract `5`; provenance `14 files`; `py_compile`; `git diff --check`. No checks skipped. |
| `make build` | passed; wrote ignored `dist/review-conductor.pyz` (4138 bytes) |
| `git diff --check` worktree and index | clean; committed exact base/head check runs after this merge commit |
| `python3 scripts/check_provenance.py` | `extraction hashes verified (14 files); Python compilation passed` |

Exact-head whitespace is `scripts/check_whitespace.py` against
`b16e0c8bc3f32e997db31caa173b846b0e9041ce` and the post-commit HEAD. That
script requires the checkout to equal HEAD, so it runs after the candidate
commit.

Provenance destination hashes were re-pinned for the merged
`tools/review_conductor.py`. This merge-forward does not claim an x-api
source branch or commit beyond the existing ledger.

## Untouched boundaries

- Adapter branch `openclaw/review-conductor-issue-687-notification-adapter-v1`
  remains `bc80b1e97f75148cd1b5be71b75f8c82cb3b9fdf`.
- No PR body edit, ready transition, human-review request, comment
  resolution, or PR merge.
- No credentials, live databases, checkouts, or proof stores outside this
  Git-only packet.
- No workflow, protection, deployment, producer, Suite, registry, or x-api
  change.
- No activation follows from this source integration.
