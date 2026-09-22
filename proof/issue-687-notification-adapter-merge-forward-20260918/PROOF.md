# Issue #687 notification adapter × core 2e40ffd merge-forward — 2026-09-18

## Ownership

- Repository: `saari-co/review-conductor`.
- Isolated worktree:
  `/Users/cp-1/.openclaw/worktrees/0dbf576623060113/review-conductor-issue-687-routing-v1`
- Stacked branch: `openclaw/review-conductor-issue-687-notification-adapter-v1`
- Verified pre-merge adapter head: `bc80b1e97f75148cd1b5be71b75f8c82cb3b9fdf`
- Exact core now merged as an ancestor:
  `2e40ffd1c8adb3062cb5af64f7a8ccaf5cab7ee5`
  (`openclaw/review-conductor-issue-687-routing-v1`).
- Frozen integration base `origin/main`: `b16e0c8bc3f32e997db31caa173b846b0e9041ce`
- Previous adapter head whose runtime behavior is preserved:
  `65b10aff8faa78550f899881505b286b9116caae`
- Previous restack head: `2e66c36cd3154c98a7e1102f6460e67a1f7f62eb`
- Preservation branch, exact full pre-split candidate:
  `openclaw/review-conductor-pr18-full-presplit-20260918` at
  `6eaf99ef11180ea523db3229c2e5c49c736bdd64`
- Sole source-changing owner: this Cursor ACP worktree. No x-api, Suite, or
  other repository was edited.
- Mode: ordinary merge of exact core `2e40ffd` into the stacked adapter.
  Runtime integration stays the `65b10aff` restore, including Copilot
  `5243987118` repairs, plus current-main PR #21 rereview behavior. No
  rebase, force-push, history rewrite, Copilot, OpenClaw, or ClawSweeper
  review request. No merge of PR #20, deploy, activation, credential,
  protection, live notification, or target-repository onboarding.

A commit cannot contain its own SHA. `candidate-manifest.json` records the
verified pre-merge adapter head, exact core, exact main, merge parents, and
per-file source hashes for the adapter slice relative to that core. The
branch head after this merge commit is the exact candidate SHA for hosted
CI.

Historical packets remain exact-head truthful:
`proof/issue-687-notification-adapter-20260918/` for `65b10aff` and
`proof/issue-687-notification-adapter-restack-20260918/` for the restack
onto `9b82ea60`.

## Starting state verified before edit

| Binding | Exact value |
| --- | --- |
| Assigned worktree start | `2e40ffd1c8adb3062cb5af64f7a8ccaf5cab7ee5` on the core branch; switched without editing it |
| Adapter branch after switch | `bc80b1e97f75148cd1b5be71b75f8c82cb3b9fdf`, clean |
| Fetched `origin/main` | `b16e0c8bc3f32e997db31caa173b846b0e9041ce` |
| Fetched adapter | `bc80b1e97f75148cd1b5be71b75f8c82cb3b9fdf` |
| Worktree | clean before merge |
| Other writers | none on the adapter branch or PR #20 timeline |

## Merge parents

Normal non-force merge of exact core into the notification adapter:

- First parent (adapter head): `bc80b1e97f75148cd1b5be71b75f8c82cb3b9fdf`
- Second parent (core head): `2e40ffd1c8adb3062cb5af64f7a8ccaf5cab7ee5`

Expected textual conflict only:

- `docs/provenance.json` — keep the adapter userland/runtime adaptation
  including Copilot `5243987118` repairs, append the PR #21 rereview
  clause, and re-pin `tools/review_conductor_userland.py` destination
  SHA-256 to the merged bytes.

Auto-merged overlap includes `docs/architecture.md`,
`docs/integration-contract.md`, `tests/test_service_runtime.py`,
`tools/review_conductor_userland.py`, and `tools/service_runtime.py`.
Those keep current-main rereview (`issue_comment` never binds, App
events include `issue_comment`, exhausted-ledger rereview refusal) and
the full #687 adapter runtime plus the three Copilot repairs.

## Restored relative to core `2e40ffd`

Because the core still does not change live runtime entry functions,
`git diff` core..adapter contains the full runtime integration:

- Userland `run_tick` route suppression.
- `notification_message`, `notification_event_identity`,
  `queue_notifications`, and `deliver_notifications`.
- Service tick wiring, `service_entrypoint.registry_provider` document-only
  loading, and registry/send guards plus the send lease.
- Fresh trusted registry/route authority immediately before every send.
- Exact tuple/epoch fencing and no stale-route sends.
- Pending-row revalidation and no duplicate delivery after uncertain outcomes.

## Copilot 5243987118 repairs

1. Malformed persisted rail/state rows produce one concise fail-closed blocked
   notification instead of aborting the queue. Direct public inputs stay
   strict.
2. Notification event identity includes complete canonical schema, kind,
   channels, and decision identity so a retired key cannot suppress the
   current notification.
3. Retry-attempt changes revalidate the complete current event key so attempt 1
   and attempt 2 cannot both send.

Final behavior matches `65b10aff8faa78550f899881505b286b9116caae` except the
boundary relocation onto core `2e40ffd` and current-main PR #21 rereview.

## Local commands and results

All commands ran in this worktree against the merge-forward adapter source.
Fixture PASS is not a deployed review PASS.

| Command | Result |
| --- | --- |
| `python3 tests/test_orchestration_outcome.py` | 35 tests OK |
| `python3 tests/test_review_conductor_userland.py` | 43 passed |
| `python3 tests/test_clawsweeper_rereview.py` | `clawsweeper rereview tests passed` |
| focused Copilot 5243987118 regressions | PASS: spark-rail blocked once; retired incomplete keys cannot suppress current; attempt 1 retires when attempt 2 is current |
| `AdmissionIngressTests.test_service_tick_wires_registry_owned_enrollment_routes` | ok |
| `AdmissionIngressTests.test_issue_comment_deliveries_never_create_bindings` | ok |
| `python3 -m compileall -q tools tests scripts` | passed |
| `make check` | passed: hygiene/CODEOWNERS; conductor/activation custom suites; rereview; userland `(43)`; orchestration `(35)`; cold hydration `(24)`; projection `(25)`; presentation `30`; original-report `31`; profiles `31`; scaffold `6`; trusted admission `23`; service runtime `94`; guard `5`; launcher transport `10`; suite launcher `20`; standalone supervisor `36`; runtime lifecycle `30`; workflow contract `5`; provenance `14 files`; `py_compile`; `git diff --check`. No checks skipped. |
| `make build` | passed; wrote ignored `dist/review-conductor.pyz` (4138 bytes) |
| `git diff --check` worktree and index | clean; committed exact base/head check runs after this merge commit |

Exact-head whitespace is `scripts/check_whitespace.py` against
`b16e0c8bc3f32e997db31caa173b846b0e9041ce` and the post-commit HEAD. The
same script is also run against the merged core tip
`2e40ffd1c8adb3062cb5af64f7a8ccaf5cab7ee5`.

Provenance destination hashes were re-pinned for the merged
`tools/review_conductor_userland.py`. This merge-forward does not claim an
x-api source branch or commit beyond the existing ledger.

## Untouched boundaries

- Core branch `openclaw/review-conductor-issue-687-routing-v1` remains
  `2e40ffd1c8adb3062cb5af64f7a8ccaf5cab7ee5`.
- No credentials, live databases, checkouts, or proof stores outside this
  Git-only packet.
- No x-api, spark-dgx, ClawSweeper, OpenClaw, or target-repository source.
- No Copilot, OpenClaw, or ClawSweeper review is requested from this
  packet. This PR stays draft/open, stacked on the PR #18 core branch, and
  does not merge or activate.

## Remaining issue

The adapter remains stacked on core `2e40ffd`. Hosted exact-head CI is the
remaining local-to-hosted gate after this merge commit; CI green is not
external review clearance. No merge, deploy, live notification, or
additional review request is made from this packet.
