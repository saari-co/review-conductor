# Epoch-less bundles after reopen — 2026-09-24

## Ownership and tuple

- Repository: `saari-co/review-conductor`
- Worktree: `/Users/cp-1/.openclaw/workspace/main/smoky/conductor-deploy-20260924`
- Branch: `fix/clawsweeper-historical-bundle-poison-20260924`
- PR: https://github.com/saari-co/review-conductor/pull/26 (draft preserved)
- Base: `0c048e227043741fcbb5b4e08c1064d9c5c100a6`
- Starting head: `e28dd167baed38c9dcdc6e77645378ca34ae7fd2`
- Capture time: 2026-09-24T19:53:51Z
- Interpreter exercised: Python 3.14.6
- This packet cannot name its own commit SHA. The branch head after the repair commit is the exact candidate for hosted CI.

Parent packet `proof/clawsweeper-same-run-empty-receipt-20260924/` remains the record of `e28dd167baed38c9dcdc6e77645378ca34ae7fd2`. Earlier packets remain historical. The review ledger is not reset. The owner explicitly continued this Copilot loop past the default two-cycle stop. No second writer was introduced.

No merge, deployment, credential, protection, live-database, private-diagnostic, or service change.

## Review ledger

Completed Copilot reviews before this repair: **5**. The cumulative count is not reset. A cancelled or metadata-failed dispatch is not a review.

| Cycle | Evidence | Disposition |
| --- | --- | --- |
| 1 | Review `5308307086` on `fcbb28afbe6fc44a1bf4516e142d76aadbd8147e` | Unsuccessful. Two inline findings, later resolved. No suppressed-findings section. |
| 2 | Review `5308595098` on `c25d6c099701c4ebaa1bab315e848a78899e15de` | Unsuccessful. Three inline findings, later resolved. No suppressed-findings section. |
| 3 | Review `5308890409` on `d550879b05c483f90d1ec233469bb29fd1fb7258` | Unsuccessful. Four inline findings, later resolved. No suppressed-findings section. |
| 4 | Review `5309209749` on `aff347c4a62f778b86b4e49d592ca6250ee5316d` | Unsuccessful. Two inline findings, later resolved on cycle 5. No suppressed-findings section. |
| 5 | Review `5309402375` by `copilot-pull-request-reviewer[bot]`, submitted 2026-09-24T19:48:52Z, state `COMMENTED`, commit `e28dd167baed38c9dcdc6e77645378ca34ae7fd2` | Unsuccessful. The body lists Open (1), high. Resolved since last review (2). It has no suppressed-findings section. |

- https://github.com/saari-co/review-conductor/pull/26#discussion_r4097791454 — required fix. After reopen, a legacy bundle that omits `review_epoch` could be selected by a current-epoch receipt that also omits `workflow_run_id`, then materialized onto the reopened head when `review_policy` is absent.

Cycle 6 is the authorized Copilot re-review after this repair is pushed and exact-head CI is terminal. It is not a completed cycle in this packet. Hosted CI run `36049497942` on the starting head succeeded and is historical for the repair head.

## Repair

A JSON object that omits `workflow_run_id` still owns a bundle that names that epoch, and an epoch-less legacy bundle at epoch 0. After reopen, that omitted key does not select an epoch-less bundle. An exact integer run id still does. The unmatched bundle is retired and does not project the reopened head.

## Local checks

- `make check`: passed. Governance, integration, rereview, activation, userland `(71)`, orchestration `(37)`, cold hydration `(24)`, projection `(25)`, presentation `(30)`, original report `(31)`, profiles `(35)`, scaffold `(6)`, trusted admission `(23)`, service runtime `(96)`, guard `(5)`, launcher transport `(10)`, suite launcher `(20)`, standalone supervisor `(36)`, runtime lifecycle `(30)`, workflow contract `(5)`, provenance `extraction hashes verified (14 files)`, Python compilation, and `git diff --check`.
- `make build`: wrote `dist/review-conductor.pyz`.
- Workflow bytes were unchanged; actionlint was not required.
- Mutants, each in a disposable copy, exit nonzero with `Ran 1 test` and `FAIL` or `ERROR` on the intended test:
  - letting an omitted run id own every epoch fails `test_epochless_bundle_does_not_own_a_reopened_omitted_run_id`
- Exact base/head whitespace is `python3 scripts/check_whitespace.py 0c048e227043741fcbb5b4e08c1064d9c5c100a6 <repair-head>` after the commit, because that checker requires checkout `HEAD` to be the event head.

## Untouched boundaries

Tuple, attempt, and running-request fences stay. A receipt that stores the exact run id can still select an epoch-less bundle. No live database, service, profile, or deployment was mutated. Hosted CI and the cycle-6 Copilot review are not claimed by this local packet.
