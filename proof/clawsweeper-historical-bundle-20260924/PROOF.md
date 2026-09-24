# Historical ClawSweeper bundle poison — 2026-09-24

## Ownership and tuple

- Repository: `saari-co/review-conductor`
- Worktree: `/Users/cp-1/.openclaw/workspace/main/smoky/conductor-deploy-20260924`
- Branch: `fix/clawsweeper-historical-bundle-poison-20260924`
- Base and starting head: `0c048e227043741fcbb5b4e08c1064d9c5c100a6`
- Capture time: 2026-09-24T17:24:58Z
- Interpreter exercised: Python 3.14.6
- This packet cannot name its own commit SHA. The branch head after the repair commit is the exact candidate for hosted CI.

No merge, deployment, credential, protection, live-database, or service change. Parent owns live operations.

## Live evidence (read-only)

Suite database `review-conductor.sqlite3` opened `mode=ro`. Current `saari-co/openclaw-smcbd-suite` #48 head is `clawsweeper_queued`, epoch 0, `review_request_id` null, projection check ids null. Dispatched action `act-9e279f270ab8c6e7bd6d78bf22d7fea3` has no `workflow_run_id`. Worker diagnostic is `UserlandError: ClawSweeper bundle does not match one current dispatched action`.

Pending `terminal_pending_verdict_bridge` rows, oldest first: success runs `35560312902`, `35859806377`, `35860482049`, `35861222536`, `35861228228`, `35862170166`, then failure `36009939846` (created `2026-09-24T14:02:54Z`, no review artifact; admission log names PR 48, base `3849c98da4af3cca63967a77c2d78901efc9a55a`, head `cf1e566a40ab43228538918fb2d1acc14419ae2e`, epoch 0). `collect_clawsweeper_terminals` runs before `reconcile_projection`. The oldest success bundle does not match one current dispatched action, the exception leaves the loop, and the current failure is never recorded.

## Ancestry not ported

`ff54d0c84b3544b986e8d434067def656e60b724` is present locally and is not an ancestor of this head. Its userland delta reorders pending runs newest-first and retires some closed notifications. Newest-first still raises on an unmatched success bundle before projection. That reorder was not ported.

## Repair

An unmatched bundle is retired to `terminal_attention_required` with a null verdict and no head change, then collection continues. A non-success run with no exact bundle fails the current head only when it is the running request, the dispatch receipt stores that run id, or the single admission job log (`Admit exact-tuple`) shows one consistent dispatch tuple for the current dispatched action. The head becomes `clawsweeper_failed`. That is not review PASS. Unproven failures stay repository alerts. Transport failure leaves the receipt pending. Dispatch requests `return_run_details` and stores `workflow_run_id` when GitHub returns it.

## Local checks

- `make check`: passed. Governance, integration, rereview, activation, userland `(50)`, orchestration `(37)`, cold hydration `(24)`, projection `(25)`, presentation `(30)`, original report `(31)`, profiles `(35)`, scaffold `(6)`, trusted admission `(23)`, service runtime `(96)`, guard `(5)`, launcher transport `(10)`, suite launcher `(20)`, standalone supervisor `(36)`, runtime lifecycle `(30)`, workflow contract `(5)`, provenance `extraction hashes verified (14 files)`, Python compilation, and `git diff --check`.
- `make build`: wrote `dist/review-conductor.pyz`.

## Untouched boundaries

Tuple, epoch, attempt, and running-request fences stay. No live database, service, profile, or deployment was mutated. Codex execution failure on run `36009939846` is a producer diagnosis and is not claimed fixed here. Hosted CI is not claimed by this local packet.

## Minimum supported live recovery

After this revision is the running collector, the next tick retires the six unmatched success receipts and, from the admission log, records `clawsweeper_failed` for #48 at the exact tuple above with request id `36009939846`. Do not mark that run PASS and do not edit the database. Maintainer `@ClawSweeper rereview` is the supported same-head recovery once the head has left `clawsweeper_queued`. Until this collector is running, the old bundle still aborts the tick.
