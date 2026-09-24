# Admission-log identity bound — 2026-09-24

## Ownership and tuple

- Repository: `saari-co/review-conductor`
- Worktree: `/Users/cp-1/.openclaw/workspace/main/smoky/conductor-deploy-20260924`
- Branch: `fix/clawsweeper-historical-bundle-poison-20260924`
- PR: https://github.com/saari-co/review-conductor/pull/26 (draft preserved)
- Base: `0c048e227043741fcbb5b4e08c1064d9c5c100a6`
- Starting head: `1ca1da8a6a59374c8592cd5ee7be6e03f4215d20`
- Capture time: 2026-09-24T17:48:54Z
- Interpreter exercised: Python 3.14.6
- This packet cannot name its own commit SHA. The branch head after the repair commit is the exact candidate for hosted CI.

Parent packet `proof/clawsweeper-historical-bundle-20260924/` remains the record of `1ca1da8a6a59374c8592cd5ee7be6e03f4215d20`. This follow-up reviewed that head's uncommitted work as untrusted interrupted source and kept it. No second writer was introduced. The repair ledger is not reset. PR 26 had no submitted reviews at capture. The recorded extraction source `saari-co/x-api` branch `openclaw/review-conductor-generalization-managed` at `48036abf1649a6fbd1738d68b23fc235893e0b68` was not found remotely at capture, so that ledger source identity stays historical and was not rewritten.

No merge, deployment, credential, protection, live-database, private-diagnostic, or reviewer-dispatch change. Parent owns live recovery.

## Repair

An admission log larger than 64KiB is not a dispatch identity. The collector no longer keeps a 64KiB prefix: a complete tuple in that prefix cannot hide a conflicting tuple later in the same log. Repeated identical complete blocks remain one identity. An incomplete block that only repeats those values is ignored. A different value in a partial block, or a second complete block, is no identity.

Job-log download reuses the existing artifact redirect transport. GitHub's job-log endpoint returns one 302 (`docs.github.com` REST workflow jobs, API version 2022-11-28). Only `productionresultssa*.blob.core.windows.net` is followed. Private-runner hosts, including `results-receiver.actions.githubusercontent.com` and `*.actions.githubusercontent.com` pipeline hosts, stay outside that allowlist. A refused redirect is no identity. This is not a broader redirect grant.

Dispatch sends `return_run_details: true`. The 2026-02-19 GitHub changelog returns HTTP 200 when that flag is set and HTTP 204 when it is omitted. The REST schema for that 200 body names `workflow_run_id`, `run_url`, and `html_url`. The receipt stores only that integer `workflow_run_id`. A 204 response, or a 200 body without that key, still counts as dispatch and leaves the run id unset.

The parent collector behavior is unchanged: unmatched bundles retire without a verdict and do not starve later runs. A no-artifact failure becomes `clawsweeper_failed` only for the exact current dispatched tuple, epoch, and run. That failure is not review PASS.

## Local checks

- `make check`: passed. Governance, integration, rereview, activation, userland `(53)`, orchestration `(37)`, cold hydration `(24)`, projection `(25)`, presentation `(30)`, original report `(31)`, profiles `(35)`, scaffold `(6)`, trusted admission `(23)`, service runtime `(96)`, guard `(5)`, launcher transport `(10)`, suite launcher `(20)`, standalone supervisor `(36)`, runtime lifecycle `(30)`, workflow contract `(5)`, provenance `extraction hashes verified (14 files)`, Python compilation, and `git diff --check`.
- `make build`: wrote `dist/review-conductor.pyz`.
- Workflow bytes were unchanged; actionlint was not required.
- Exact base/head whitespace is `python3 scripts/check_whitespace.py 0c048e227043741fcbb5b4e08c1064d9c5c100a6 <repair-head>` after the commit, because that checker requires checkout `HEAD` to be the event head.

## Untouched boundaries

Tuple, epoch, attempt, and running-request fences stay. The artifact redirect allowlist is not widened. No live database, service, profile, or deployment was mutated. The producer cause of run `36009939846` remains unknown. A prior scoped private diagnostic inspection found no entries and was not repeated. Hosted CI is not claimed by this local packet.

## Activation gate

This commit does not activate the collector. After this revision is the running collector, the next tick may retire the six unmatched success receipts and record `clawsweeper_failed` for `saari-co/openclaw-smcbd-suite` #48 at base `3849c98da4af3cca63967a77c2d78901efc9a55a`, head `cf1e566a40ab43228538918fb2d1acc14419ae2e`, epoch 0, run `36009939846`, only when that run is the exact current dispatched attempt and the admission log is one consistent in-bound tuple on the existing blob redirect, or the receipt already stores that run id. An oversize log, a conflicting partial or second block, or a private-runner redirect is not that identity and must not be marked PASS. Do not edit the database. Maintainer `@ClawSweeper rereview` is the supported same-head recovery once the head has left `clawsweeper_queued`.
