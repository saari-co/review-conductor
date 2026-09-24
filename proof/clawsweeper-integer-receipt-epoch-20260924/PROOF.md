# Integer receipts, admission-job shape, and bounded report epochs — 2026-09-24

## Ownership and tuple

- Repository: `saari-co/review-conductor`
- Worktree: `/Users/cp-1/.openclaw/workspace/main/smoky/conductor-deploy-20260924`
- Branch: `fix/clawsweeper-historical-bundle-poison-20260924`
- PR: https://github.com/saari-co/review-conductor/pull/26 (draft preserved)
- Base: `0c048e227043741fcbb5b4e08c1064d9c5c100a6`
- Starting head: `d550879b05c483f90d1ec233469bb29fd1fb7258`
- Capture time: 2026-09-24T19:16:45Z
- Interpreter exercised: Python 3.14.6
- This packet cannot name its own commit SHA. The branch head after the repair commit is the exact candidate for hosted CI.

Parent packet `proof/clawsweeper-receipt-job-page-20260924/` remains the record of `d550879b05c483f90d1ec233469bb29fd1fb7258`. Earlier packets remain historical. The review ledger is not reset. The owner explicitly continued this Copilot loop past the default two-cycle stop. No second writer was introduced. The interrupted working tree was inspected and adopted; it was not reset.

No merge, deployment, credential, protection, live-database, private-diagnostic, or service change.

## Review ledger

Completed Copilot reviews before this repair: **3**. The cumulative count is not reset. A cancelled or metadata-failed dispatch is not a review.

| Cycle | Evidence | Disposition |
| --- | --- | --- |
| 1 | Review `5308307086` on `fcbb28afbe6fc44a1bf4516e142d76aadbd8147e` | Unsuccessful. Two inline findings, later marked resolved. No suppressed-findings section. |
| 2 | Review `5308595098` on `c25d6c099701c4ebaa1bab315e848a78899e15de` | Unsuccessful. Three inline findings, later marked resolved on cycle 3. No suppressed-findings section. |
| 3 | Review `5308890409` by `copilot-pull-request-reviewer[bot]`, submitted 2026-09-24T19:02:49Z, state `COMMENTED`, commit `d550879b05c483f90d1ec233469bb29fd1fb7258` | Unsuccessful. The body lists Open (4): 2 high and 2 medium. Resolved since last review (3). It has no suppressed-findings section. |

- https://github.com/saari-co/review-conductor/pull/26#discussion_r4097371918 — required fix. A string `workflow_run_id` such as `"3084"` was accepted as a stored identity because the comparison used `str(recorded)`.
- https://github.com/saari-co/review-conductor/pull/26#discussion_r4097371979 — required fix. The admission-log path reached the same string comparison, so a present non-integer run id passed the exact-attempt fence.
- https://github.com/saari-co/review-conductor/pull/26#discussion_r4097372032 — required fix. A matching admission job with a missing or malformed `id` raised `ContractError` outside the collector's `GitHubApiError` handler and could abort the tick.
- https://github.com/saari-co/review-conductor/pull/26#discussion_r4097372087 — required fix. A digit-only report epoch of unbounded length was passed to SQLite by `clawsweeper_action`. Values past the signed 64-bit range raise `OverflowError` and abort later collection.

Cycle 4 is the authorized Copilot re-review after this repair is pushed and exact-head CI is terminal. It is not a completed cycle in this packet. Hosted CI run `36044202363` on the starting head succeeded and is historical for the repair head.

## Repair

A present `workflow_run_id` must be a non-boolean integer equal to that run before it is a stored hit or passes the admission fence. A JSON object that omits the key still owns the run. JSON `null`, an array, a string, a bool, or invalid JSON does not. A matching admission job whose id is not a positive integer raises `GitHubApiError`, so the receipt stays `terminal_pending_verdict_bridge` and later receipts still collect. The job is not treated as absent. A report epoch uses the admission tuple's ten-digit bound. An out-of-bound epoch, including one past SQLite's signed 64-bit range, leaves that receipt pending instead of aborting later collection.

## Local checks

- `make check`: passed. Governance, integration, rereview, activation, userland `(66)`, orchestration `(37)`, cold hydration `(24)`, projection `(25)`, presentation `(30)`, original report `(31)`, profiles `(35)`, scaffold `(6)`, trusted admission `(23)`, service runtime `(96)`, guard `(5)`, launcher transport `(10)`, suite launcher `(20)`, standalone supervisor `(36)`, runtime lifecycle `(30)`, workflow contract `(5)`, provenance `extraction hashes verified (14 files)`, Python compilation, and `git diff --check`.
- `make build`: wrote `dist/review-conductor.pyz`.
- Workflow bytes were unchanged; actionlint was not required.
- Mutants, each in a disposable copy, exit nonzero with `Ran 1 test` and `FAIL` or `ERROR` on the intended test:
  - restoring string comparison for a stored hit fails `test_string_receipt_run_id_is_not_a_stored_identity`
  - restoring string comparison on the admission fence fails `test_string_receipt_run_id_does_not_pass_the_admission_fence`
  - treating a malformed admission-job id as absent fails `test_malformed_admission_job_id_stays_pending_and_collection_continues`
  - dropping the ten-digit epoch bound fails `test_overlong_bundle_epoch_does_not_abort_later_collection`
- Exact base/head whitespace is `python3 scripts/check_whitespace.py 0c048e227043741fcbb5b4e08c1064d9c5c100a6 <repair-head>` after the commit, because that checker requires checkout `HEAD` to be the event head.

## Untouched boundaries

Tuple, epoch, attempt, and running-request fences stay. The artifact redirect allowlist is not widened. A jobs `Link` whose query is not `per_page=100` then an optional page fails closed instead of being fetched. No live database, service, profile, or deployment was mutated. A JSON object that omits `workflow_run_id` can still bind a same-tuple no-artifact failure for that current epoch. An in-flight current-epoch dispatch that has not stored a different integer run id still leaves the receipt pending. Hosted CI and the cycle-4 Copilot review are not claimed by this local packet.
