# Same-run job pages and empty receipts — 2026-09-24

## Ownership and tuple

- Repository: `saari-co/review-conductor`
- Worktree: `/Users/cp-1/.openclaw/workspace/main/smoky/conductor-deploy-20260924`
- Branch: `fix/clawsweeper-historical-bundle-poison-20260924`
- PR: https://github.com/saari-co/review-conductor/pull/26 (draft preserved)
- Base: `0c048e227043741fcbb5b4e08c1064d9c5c100a6`
- Starting head: `aff347c4a62f778b86b4e49d592ca6250ee5316d`
- Capture time: 2026-09-24T19:37:42Z
- Interpreter exercised: Python 3.14.6
- This packet cannot name its own commit SHA. The branch head after the repair commit is the exact candidate for hosted CI.

Parent packet `proof/clawsweeper-integer-receipt-epoch-20260924/` remains the record of `aff347c4a62f778b86b4e49d592ca6250ee5316d`. Earlier packets remain historical. The review ledger is not reset. The owner explicitly continued this Copilot loop past the default two-cycle stop. No second writer was introduced.

No merge, deployment, credential, protection, live-database, private-diagnostic, or service change.

## Review ledger

Completed Copilot reviews before this repair: **4**. The cumulative count is not reset. A cancelled or metadata-failed dispatch is not a review.

| Cycle | Evidence | Disposition |
| --- | --- | --- |
| 1 | Review `5308307086` on `fcbb28afbe6fc44a1bf4516e142d76aadbd8147e` | Unsuccessful. Two inline findings, later resolved. No suppressed-findings section. |
| 2 | Review `5308595098` on `c25d6c099701c4ebaa1bab315e848a78899e15de` | Unsuccessful. Three inline findings, later resolved. No suppressed-findings section. |
| 3 | Review `5308890409` on `d550879b05c483f90d1ec233469bb29fd1fb7258` | Unsuccessful. Four inline findings, later resolved on cycle 4. No suppressed-findings section. |
| 4 | Review `5309209749` by `copilot-pull-request-reviewer[bot]`, submitted 2026-09-24T19:31:48Z, state `COMMENTED`, commit `aff347c4a62f778b86b4e49d592ca6250ee5316d` | Unsuccessful. The body lists Open (2), both high. Resolved since last review (4). It has no suppressed-findings section. |

- https://github.com/saari-co/review-conductor/pull/26#discussion_r4097641445 — required fix. A jobs `Link` rel=next was allowlisted for any run id, so a page could be read from another workflow run and bind this failure to the wrong pull request.
- https://github.com/saari-co/review-conductor/pull/26#discussion_r4097641519 — required fix. `receipt_json or "{}"` treated SQL NULL and `""` as an object that omits `workflow_run_id`, so the receipt could own a current bundle and pass the admission fence. The same coercion was on the fence path.

Cycle 5 is the authorized Copilot re-review after this repair is pushed and exact-head CI is terminal. It is not a completed cycle in this packet. Hosted CI run `36047509904` on the starting head succeeded and is historical for the repair head.

## Repair

Each jobs next page must stay on `/repos/<repository>/actions/runs/<this-run>/jobs` before it is followed. A link onto another run raises and leaves the lookup incomplete. An empty string or SQL NULL is not a JSON object. Only a real object may use the missing-key rule. JSON `null`, an array, and invalid JSON still own nothing. A JSON object that omits `workflow_run_id` still owns the run.

## Local checks

- `make check`: passed. Governance, integration, rereview, activation, userland `(69)`, orchestration `(37)`, cold hydration `(24)`, projection `(25)`, presentation `(30)`, original report `(31)`, profiles `(35)`, scaffold `(6)`, trusted admission `(23)`, service runtime `(96)`, guard `(5)`, launcher transport `(10)`, suite launcher `(20)`, standalone supervisor `(36)`, runtime lifecycle `(30)`, workflow contract `(5)`, provenance `extraction hashes verified (14 files)`, Python compilation, and `git diff --check`.
- `make build`: wrote `dist/review-conductor.pyz`.
- Workflow bytes were unchanged; actionlint was not required.
- Mutants, each in a disposable copy, exit nonzero with `Ran 1 test` and `FAIL` or `ERROR` on the intended test:
  - following another run's jobs page fails `test_jobs_next_link_must_stay_on_the_requested_run`
  - coercing an empty receipt to `{}` fails `test_empty_receipt_does_not_own_a_current_bundle`
- Exact base/head whitespace is `python3 scripts/check_whitespace.py 0c048e227043741fcbb5b4e08c1064d9c5c100a6 <repair-head>` after the commit, because that checker requires checkout `HEAD` to be the event head.

## Untouched boundaries

Tuple, epoch, attempt, and running-request fences stay. The artifact redirect allowlist is not widened. The generic jobs allowlist still accepts a run id; the same-run check is the additional fence before a next page is fetched. No live database, service, profile, or deployment was mutated. A JSON object that omits `workflow_run_id` can still bind a same-tuple bundle or no-artifact failure for that current epoch. Hosted CI and the cycle-5 Copilot review are not claimed by this local packet.
