# Current-epoch ClawSweeper bundle retirement — 2026-09-24

## Ownership and tuple

- Repository: `saari-co/review-conductor`
- Worktree: `/Users/cp-1/.openclaw/workspace/main/smoky/conductor-deploy-20260924`
- Branch: `fix/clawsweeper-historical-bundle-poison-20260924`
- PR: https://github.com/saari-co/review-conductor/pull/26 (draft preserved)
- Base: `0c048e227043741fcbb5b4e08c1064d9c5c100a6`
- Starting head: `fcbb28afbe6fc44a1bf4516e142d76aadbd8147e`
- Capture time: 2026-09-24T18:22:21Z
- Interpreter exercised: Python 3.14.6
- This packet cannot name its own commit SHA. The branch head after the repair commit is the exact candidate for hosted CI.

Parent packets `proof/clawsweeper-historical-bundle-20260924/` and `proof/clawsweeper-admission-log-identity-20260924/` remain the records of `1ca1da8a6a59374c8592cd5ee7be6e03f4215d20` and `fcbb28afbe6fc44a1bf4516e142d76aadbd8147e`. The repair ledger is not reset. No second writer was introduced.

No merge, deployment, credential, protection, live-database, private-diagnostic, or service change.

## Review ledger

Completed unsuccessful Copilot cycles before this repair: **1**. Cap remains two.

| Cycle | Evidence | Disposition |
| --- | --- | --- |
| 1 | Review `5308307086` / `PRR_kwDOUXe3188AAAABPGZWjg` by `copilot-pull-request-reviewer[bot]`, submitted 2026-09-24T18:09:19Z, state `COMMENTED`, commit `fcbb28afbe6fc44a1bf4516e142d76aadbd8147e` | Unsuccessful. Two inline high findings. The review body lists Open (2) and has no suppressed-findings section. Inline comments are `4096884869` and `4096884939`. |

- https://github.com/saari-co/review-conductor/pull/26#discussion_r4096884869 — a success bundle was retired while the matching action was still `dispatching`.
- https://github.com/saari-co/review-conductor/pull/26#discussion_r4096884939 — retirement did not keep an earlier epoch's still-dispatched action out of the current owner set. Reopen leaves `dispatched` actions in place and increments `review_epoch` on the same head row.

Cycle 2 is the authorized Copilot re-review after this repair is pushed and exact-head CI is terminal. It is not a completed cycle in this packet. Request registration for cycle 1 was timeline `review_requested` at 2026-09-24T18:05:08Z and `copilot_work_started` at 2026-09-24T18:05:46Z. Hosted CI run `36037363260` on the starting head succeeded and is historical for the repair head.

## Repair

`current_epoch_clawsweeper_actions` joins the action to the current head on `review_epoch`. A report epoch, when present, must be that same epoch. A dispatched receipt that stores `workflow_run_id` binds only that run. `pending`, `preparing`, `dispatching`, and `reconcile_required` leave the receipt `terminal_pending_verdict_bridge`. Several current-epoch dispatches that do not exclude this run also stay pending. Anything else is retired with no verdict and no head change. Collection continues.

## Local checks

- `make check`: passed. Governance, integration, rereview, activation, userland `(57)`, orchestration `(37)`, cold hydration `(24)`, projection `(25)`, presentation `(30)`, original report `(31)`, profiles `(35)`, scaffold `(6)`, trusted admission `(23)`, service runtime `(96)`, guard `(5)`, launcher transport `(10)`, suite launcher `(20)`, standalone supervisor `(36)`, runtime lifecycle `(30)`, workflow contract `(5)`, provenance `extraction hashes verified (14 files)`, Python compilation, and `git diff --check`.
- `make build`: wrote `dist/review-conductor.pyz`.
- Workflow bytes were unchanged; actionlint was not required.
- Mutants, each in a disposable copy, exit nonzero with `Ran 1 test` and `FAIL` on the intended test:
  - removing `heads.review_epoch = actions.review_epoch -- current-head epoch` fails `test_reopened_epoch_retires_previous_run_and_keeps_current_bundle`
  - removing `dispatching` from `IN_FLIGHT_CLAWSWEEPER_STATUSES` fails `test_inflight_dispatch_bundle_stays_pending`
- Exact base/head whitespace is `python3 scripts/check_whitespace.py 0c048e227043741fcbb5b4e08c1064d9c5c100a6 <repair-head>` after the commit, because that checker requires checkout `HEAD` to be the event head.

## Untouched boundaries

Tuple, epoch, attempt, and running-request fences stay. The artifact redirect allowlist is not widened. No live database, service, profile, or deployment was mutated. A dispatch receipt that never stores `workflow_run_id` can still bind a same-SHA bundle for that current epoch. Hosted CI and the cycle-2 Copilot review are not claimed by this local packet.
