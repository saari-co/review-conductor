# Dispatch identity epoch bound — 2026-09-24

## Ownership and tuple

- Repository: `saari-co/review-conductor`
- Worktree: `/Users/cp-1/.openclaw/workspace/main/smoky/conductor-deploy-20260924`
- Branch: `fix/clawsweeper-historical-bundle-poison-20260924`
- PR: https://github.com/saari-co/review-conductor/pull/26 (draft preserved)
- Base: `0c048e227043741fcbb5b4e08c1064d9c5c100a6`
- Starting head: `4298ee37d103c5015e4c0d2b4710eeccdd80db53`
- Capture time: 2026-09-24T20:11:24Z
- Interpreter exercised: Python 3.14.6
- This packet cannot name its own commit SHA. The branch head after the repair commit is the exact candidate for hosted CI.

Parent packet `proof/clawsweeper-epochless-reopen-20260924/` remains the record of `4298ee37d103c5015e4c0d2b4710eeccdd80db53`. Earlier packets remain historical. The review ledger is not reset. The owner explicitly continued this Copilot loop past the default two-cycle stop. No second writer was introduced.

No merge, deployment, credential, protection, live-database, private-diagnostic, or service change.

## Review ledger

Completed Copilot reviews before this repair: **6**. The cumulative count is not reset. A cancelled or metadata-failed dispatch is not a review.

| Cycle | Evidence | Disposition |
| --- | --- | --- |
| 1 | Review `5308307086` on `fcbb28afbe6fc44a1bf4516e142d76aadbd8147e` | Unsuccessful. Two inline findings, later resolved. No suppressed-findings section. |
| 2 | Review `5308595098` on `c25d6c099701c4ebaa1bab315e848a78899e15de` | Unsuccessful. Three inline findings, later resolved. No suppressed-findings section. |
| 3 | Review `5308890409` on `d550879b05c483f90d1ec233469bb29fd1fb7258` | Unsuccessful. Four inline findings, later resolved. No suppressed-findings section. |
| 4 | Review `5309209749` on `aff347c4a62f778b86b4e49d592ca6250ee5316d` | Unsuccessful. Two inline findings, later resolved. No suppressed-findings section. |
| 5 | Review `5309402375` on `e28dd167baed38c9dcdc6e77645378ca34ae7fd2` | Unsuccessful. One inline finding, later resolved on cycle 6. No suppressed-findings section. |
| 6 | Review `5309609799` by `copilot-pull-request-reviewer[bot]`, submitted 2026-09-24T20:05:39Z, state `COMMENTED`, commit `4298ee37d103c5015e4c0d2b4710eeccdd80db53` | Unsuccessful. The body lists Open (1), medium. Resolved since last review (1). The overview sentence says three moderate findings remain; the Open list and review threads contain one unresolved comment. Every earlier thread is resolved. The body has no suppressed-findings section. |

- https://github.com/saari-co/review-conductor/pull/26#discussion_r4097954065 — required fix. `_dispatch_identity_from_report` accepted any non-negative integer epoch from `clawsweeper_dispatch_identity`. An epoch past SQLite's signed range could raise `OverflowError` inside the later action lookup and abort collection.

Cycle 7 is the authorized Copilot re-review after this repair is pushed and exact-head CI is terminal. It is not a completed cycle in this packet. Hosted CI run `36051287926` on the starting head succeeded and is historical for the repair head.

## Repair

A dispatch identity epoch uses the admission tuple's ten-digit bound before the identity is returned. `9999999999` remains a valid epoch. `10**10` and any larger integer raise `GitHubApiError`, so the no-artifact receipt stays `terminal_pending_verdict_bridge` and later receipts still collect. The value is not bound into SQLite.

## Local checks

- `make check`: passed. Governance, integration, rereview, activation, userland `(72)`, orchestration `(37)`, cold hydration `(24)`, projection `(25)`, presentation `(30)`, original report `(31)`, profiles `(35)`, scaffold `(6)`, trusted admission `(23)`, service runtime `(96)`, guard `(5)`, launcher transport `(10)`, suite launcher `(20)`, standalone supervisor `(36)`, runtime lifecycle `(30)`, workflow contract `(5)`, provenance `extraction hashes verified (14 files)`, Python compilation, and `git diff --check`.
- `make build`: wrote `dist/review-conductor.pyz`.
- Workflow bytes were unchanged; actionlint was not required.
- Mutants, each in a disposable copy, exit nonzero with `Ran 1 test` and `FAIL` or `ERROR` on the intended test:
  - relaxing the dispatch epoch bound to `epoch >= 2**63` fails `test_overlong_dispatch_epoch_stays_pending_and_collection_continues`
- Exact base/head whitespace is `python3 scripts/check_whitespace.py 0c048e227043741fcbb5b4e08c1064d9c5c100a6 <repair-head>` after the commit, because that checker requires checkout `HEAD` to be the event head.

## Untouched boundaries

Tuple, attempt, and running-request fences stay. The admission-log parser still rejects an eleven-digit epoch before the runtime client returns an identity. No live database, service, profile, or deployment was mutated. Hosted CI and the cycle-7 Copilot review are not claimed by this local packet.
