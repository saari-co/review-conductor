# Receipt shape, job pages, and in-flight failure retirement — 2026-09-24

## Ownership and tuple

- Repository: `saari-co/review-conductor`
- Worktree: `/Users/cp-1/.openclaw/workspace/main/smoky/conductor-deploy-20260924`
- Branch: `fix/clawsweeper-historical-bundle-poison-20260924`
- PR: https://github.com/saari-co/review-conductor/pull/26 (draft preserved)
- Base: `0c048e227043741fcbb5b4e08c1064d9c5c100a6`
- Starting head: `c25d6c099701c4ebaa1bab315e848a78899e15de`
- Capture time: 2026-09-24T18:51:07Z
- Interpreter exercised: Python 3.14.6
- This packet cannot name its own commit SHA. The branch head after the repair commit is the exact candidate for hosted CI.

Parent packet `proof/clawsweeper-bundle-epoch-retirement-20260924/` remains the record of `c25d6c099701c4ebaa1bab315e848a78899e15de`. Earlier packets remain historical. The review ledger is not reset. The runtime saturating repair-cycle policy is unchanged. No second writer was introduced.

No merge, deployment, credential, protection, live-database, private-diagnostic, or service change.

## Review ledger

Completed Copilot cycles before this repair: **2**. The cumulative count is not reset.

| Cycle | Evidence | Disposition |
| --- | --- | --- |
| 1 | Review `5308307086` on `fcbb28afbe6fc44a1bf4516e142d76aadbd8147e` | Unsuccessful. Two inline findings, later marked resolved on cycle 2. No suppressed-findings section. |
| 2 | Review `5308595098` / `PRR_kwDOUXe3188AAAABPGq7mg` by `copilot-pull-request-reviewer[bot]`, submitted 2026-09-24T18:35:54Z, state `COMMENTED`, commit `c25d6c099701c4ebaa1bab315e848a78899e15de` | Unsuccessful. The body lists Open (3) and Resolved since last review (2). It has no suppressed-findings section. Inline comments are `4097130107`, `4097130189`, and `4097130258`. |

- https://github.com/saari-co/review-conductor/pull/26#discussion_r4097130107 — required fix. A non-object receipt (`null` or `[]`) was treated as a missing run id and owned the run.
- https://github.com/saari-co/review-conductor/pull/26#discussion_r4097130189 — required fix. Admission-job lookup read only the first jobs page.
- https://github.com/saari-co/review-conductor/pull/26#discussion_r4097130258 — required fix. A no-artifact failure receipt was retired while the current-epoch action was still `pending`, `preparing`, `dispatching`, or `reconcile_required`.

Cycle 3 is the authorized Copilot re-review after this repair is pushed and exact-head CI is terminal. It is not a completed cycle in this packet. Hosted CI run `36040855059` on the starting head succeeded and is historical for the repair head.

## Repair

A JSON object that omits `workflow_run_id` still owns the run. JSON `null`, an array, or any other non-object owns nothing. Admission-job lookup requests `per_page=100` and follows allowlisted `Link` rel=next pages through the existing ten-page cap. An incomplete listing raises and leaves the receipt pending. A no-artifact failure stays `terminal_pending_verdict_bridge` while a current-epoch in-flight dispatch has not stored a different integer run id. A proven tuple limits that check to that tuple. The operator alert is queued only when the receipt is actually retired.

## Local checks

- `make check`: passed. Governance, integration, rereview, activation, userland `(62)`, orchestration `(37)`, cold hydration `(24)`, projection `(25)`, presentation `(30)`, original report `(31)`, profiles `(35)`, scaffold `(6)`, trusted admission `(23)`, service runtime `(96)`, guard `(5)`, launcher transport `(10)`, suite launcher `(20)`, standalone supervisor `(36)`, runtime lifecycle `(30)`, workflow contract `(5)`, provenance `extraction hashes verified (14 files)`, Python compilation, and `git diff --check`.
- `make build`: wrote `dist/review-conductor.pyz`.
- Workflow bytes were unchanged; actionlint was not required.
- Mutants, each in a disposable copy, exit nonzero with `Ran 1 test` and `FAIL` on the intended test:
  - removing the non-object receipt guard fails `test_malformed_receipt_does_not_own_a_current_bundle`
  - disabling the no-artifact in-flight fence fails `test_inflight_no_artifact_failure_stays_pending`
  - returning the first jobs page fails `test_admission_job_on_a_later_page_is_the_dispatch_identity`
  - reading past the jobs page cap fails `test_admission_job_lookup_does_not_read_past_the_page_cap`
- Exact base/head whitespace is `python3 scripts/check_whitespace.py 0c048e227043741fcbb5b4e08c1064d9c5c100a6 <repair-head>` after the commit, because that checker requires checkout `HEAD` to be the event head.

## Untouched boundaries

Tuple, epoch, attempt, and running-request fences stay. The artifact redirect allowlist is not widened. A jobs `Link` whose query is not `per_page=100` then an optional page fails closed instead of being fetched. No live database, service, profile, or deployment was mutated. Without a proven tuple, any current-epoch in-flight dispatch that has not stored a different run id keeps an unbound failure pending. A JSON object that omits `workflow_run_id` can still bind a same-tuple bundle for that current epoch. Hosted CI and the cycle-3 Copilot review are not claimed by this local packet.
