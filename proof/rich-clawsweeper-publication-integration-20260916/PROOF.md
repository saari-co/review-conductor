# Rich ClawSweeper publication × main integration — 2026-09-16

## Ownership

- Repository: `saari-co/review-conductor`.
- Isolated worktree:
  `/Users/cp-1/Developer/worktrees/review-conductor-codex-restore-rich-clawsweeper-publication-20260915`
- Branch: `codex/restore-rich-clawsweeper-publication-20260915`
- Sole source-writing owner: this worktree. No other writer appeared on the
  assigned branch. Root retains PR publication, reviewer-request, and merge
  closure.
- Mode: source integration only. No merge of PR #15, deploy, activation,
  settings, credentials, automated reviewer request, or third Copilot /
  replacement broad review.

A commit cannot contain its own SHA. `candidate-manifest.json` records the
required start tuple and per-file source hashes. The branch head after this
merge commit is the exact candidate SHA for later CI and owner review.

## Starting state verified before edit

| Binding | Exact value |
| --- | --- |
| Local/remote head | `c5e3432d180a22c6221adf3739aeef7fb3792c62` |
| Frozen integration base `origin/main` | `c51ed12afce59eb77fa062796840bd5a1b054f3f` |
| PR merge-base (historical) | `8cefc3548d40b3868263cacd54a9568d51fbe67f` |
| Worktree | clean |
| Review ledger | **2/2** Copilot rounds closed |
| Prior Copilot threads | all three resolved |

Older proof `proof/rich-clawsweeper-publication-20260915/` remains historical.
The final proof-status repair on `c5e3432` was TESTED and is **NOT EXTERNALLY
RE-REVIEWED**.

## Merge parents

Normal non-force merge of exact main into the PR branch:

- First parent (PR head): `c5e3432d180a22c6221adf3739aeef7fb3792c62`
- Second parent (main): `c51ed12afce59eb77fa062796840bd5a1b054f3f`

Known textual conflict: `docs/provenance.json`. Auto-merged overlap:
`Makefile`, `docs/integration-contract.md`, `tools/review_conductor.py`,
`tools/review_conductor_runtime.py`. Tests from main landed beside the PR
presentation suite; Makefile now runs both `test_clawsweeper_presentation.py`
and `test_runtime_lifecycle.py`.

## Three prior Copilot invariants retained

1. [4021204883](https://github.com/saari-co/review-conductor/pull/15#discussion_r4021204883):
   `safe_diagram` still matches only the declaration case-insensitively.
2. [4021204916](https://github.com/saari-co/review-conductor/pull/15#discussion_r4021204916):
   architecture and integration-contract still supersede the old
   status-only/comment split; Conductor owns bounded publication only.
3. [4021290511](https://github.com/saari-co/review-conductor/pull/15#discussion_r4021290511):
   native proof-status vocabulary remains the producer's exact six values;
   obsolete `not_needed` / `failed` / `required` stay rejected.

## Integration overlap review

Rich publication still binds exact request / `workflow_run_id` / digest /
tuple / epoch / actor. The landed ClawSweeper `waiting_human` fences are
not weakened:

- `waiting_human` still accepts only `defer` / `reject_false_positive` with
  matching request/rail/epoch/actor and the exact bound `human_gate`
  terminal. OpenClaw `human_gate`, findings-origin `waiting_human`,
  exhausted `required_fix`, and `required_fix` from `waiting_human` stay
  fail-closed.
- Accepted, effective, and projected ClawSweeper quality still bind
  `workflow_run_id` to the exact clawsweeper `review_request_id`.
- Rich presentation now selects the exact bound `workflow_run_id` among
  same-epoch terminals. A later leftover collector run cannot steal
  publication; a same-epoch run mismatch still fails closed before writes.
- Native scores still do not regrade, adjudicate, or mint merge authority.
  Owner adjudication to `ready_for_human_merge` reuses `effective_quality`
  and does not dispatch a new reviewer or epoch.

Required compatibility repair only: `accepted_clawsweeper_presentation`
plus two focused tests. No refactor of unrelated code.

## Local commands and results

All commands ran in this worktree against the candidate source. Fixture PASS
is not a deployed review PASS.

| Command | Result |
| --- | --- |
| Focused presentation interaction (leftover exact-run bind, waiting_human owner adjudication, receipt conflicts, collector repeat, proof-deficient) | 5 passed |
| `python3 tests/test_review_conductor.py` selected waiting_human happy path, stale/mismatch, human_gate-terminal provenance, mismatched quality, OpenClaw fail-closed | `review conductor integration tests passed` |
| `python3 tests/test_review_result_projection.py` selected exact-adjudication, proof-gap, mismatched quality | `review result projection tests passed (3)` |
| `python3 scripts/check_provenance.py` | `extraction hashes verified (14 files); Python compilation passed` |
| `make check` | passed: hygiene/CODEOWNERS; conductor/activation custom suites; userland `(22)`; projection `(25)`; presentation `30`; original-report `31`; profiles `31`; cold hydration `24`; scaffold `6`; trusted admission `18`; service runtime `72`; guard `5`; launcher transport `10`; suite launcher `20`; standalone supervisor `36`; runtime lifecycle `30`; workflow contract `5`; provenance `14 files`; `py_compile`; `git diff --check`. No checks skipped. |
| `make build` | wrote ignored `dist/review-conductor.pyz` (4138 bytes) |
| `git diff --check` worktree and index | clean; committed exact base/head check runs after this merge commit |
| Workflows | unchanged; actionlint not required |

Copilot budget remains **2/2**. This integration is **TESTED**,
**INDEPENDENT CHECKLIST ACCEPTED**, and **NOT EXTERNALLY RE-REVIEWED**.
No third Copilot or replacement precommit-review gate is requested.

## Untouched boundaries

- No PR body edit, ready transition, human-review request, comment
  resolution, or PR merge.
- No credentials, live databases, checkouts, or proof stores outside this
  Git-only packet.
- No workflow, protection, deployment, producer, Suite, or registry change.
- No activation follows from this source integration.
