# Issue #687 PR #20 frozen post-cap adapter repair — 2026-09-18

## Ownership

- Repository: `saari-co/review-conductor`.
- Isolated worktree:
  `/Users/cp-1/.openclaw/worktrees/0dbf576623060113/review-conductor-issue-687-routing-v1`
- Stacked branch: `openclaw/review-conductor-issue-687-notification-adapter-v1`
- Frozen integration base `origin/main`:
  `b16e0c8bc3f32e997db31caa173b846b0e9041ce`
- Verified pre-merge adapter head: `a5ecee12a468ff62cd6f881a39f408f0f511995f`
- Ordinary merge of repaired PR #18 core:
  `009279c80314886f0bee6f314024e6e6e9a14769`
  (`openclaw/review-conductor-issue-687-routing-v1`).
- Merge commit that made that core an ancestor:
  `724c6a92584f73c8cf01f48f855c34186651e8a4`
- Sole source-changing owner: this Cursor ACP worktree. No x-api, Suite,
  or other repository was edited.
- Mode: owner-authorized frozen post-cap repair of draft PR #20 after the
  PR #18 core repair. Review ledger remains exhausted at 2/2 and is not
  reset. No rebase, amend, force-push, history rewrite, Copilot, OpenClaw,
  or ClawSweeper review request. No merge of PR #20, deploy, activation,
  credential, protection, live notification, readiness flip, or
  target-repository onboarding.

A commit cannot contain its own SHA. `candidate-manifest.json` records the
frozen main base, the merge that absorbed repaired core `009279c`, and
per-file hashes for this hold_send repair. The branch head after this
ordinary commit is the exact candidate SHA for hosted CI.

Historical packets remain exact-head truthful:
`proof/issue-687-notification-adapter-merge-forward-20260918/` remains the
explanation of merging core `2e40ffd` / main `b16e0c8` and does not embed
a later receipt commit's own SHA.

## Why this repair exists

After the ordinary merge of repaired core `009279c`,
`RegistryRouteLease.hold_send()` still re-resolved the wrapped source on
every nested or overlapping hold. A provider sequence of outer app=1,
inner app=2, current app=1 yielded inner pin app=2 while `current()` and
the outer hold stayed on app=1. Nested/overlapping holds must reuse the
already pinned registry generation. Replacement remains fail-closed while
any hold is active. The documented cooperative boundary is unchanged.

Direct nested reproduction is now negative: inner pin equals current/outer
app=1 while a hold is active.

## Preserved adapter behavior

- `65b10aff` runtime restore of live queue, delivery, `run_tick`,
  `run_service_tick`, `service_entrypoint.registry_provider`, send lease,
  and notifier-transport authority.
- Copilot `5243987118` repairs: malformed persisted rail/state rows notify
  blocked once; complete current event identity; retry-attempt revalidation.
- Current-main PR #21 rereview behavior.

## Local commands and results

All commands ran in this worktree against the repaired adapter candidate
source. Fixture PASS is not a deployed review PASS.

| Command | Result |
| --- | --- |
| direct nested `hold_send` reproduction | now inner=current=outer app=1 |
| `AdmissionIngressTests.test_nested_hold_send_reuses_already_pinned_generation` | ok |
| `AdmissionIngressTests.test_registry_route_lease_pins_current_generation_through_hold_send` | ok |
| precise nested-hold mutant | killed by FAIL on the intended test (`inner is outer`) |
| `python3 tests/test_orchestration_outcome.py` | 37 tests OK |
| `python3 tests/test_review_conductor_userland.py` | 43 passed |
| `python3 -m compileall -q tools tests scripts` | passed |
| worktree/index `git diff --check` | clean |
| `python3 scripts/check_provenance.py` | `extraction hashes verified (14 files); Python compilation passed` |
| `make check` | passed: hygiene/CODEOWNERS; conductor/activation custom suites; rereview; userland `(43)`; orchestration `(37)`; cold hydration `(24)`; projection `(25)`; presentation `30`; original-report `31`; profiles `31`; scaffold `6`; trusted admission `23`; service runtime `95`; guard `5`; launcher transport `10`; suite launcher `20`; standalone supervisor `36`; runtime lifecycle `30`; workflow contract `5`; provenance `14 files`; `py_compile`; `git diff --check`. No checks skipped. |
| `make build` | passed; wrote ignored `dist/review-conductor.pyz` (4138 bytes) |

Exact-head whitespace is `scripts/check_whitespace.py` against
`b16e0c8bc3f32e997db31caa173b846b0e9041ce` and the post-commit HEAD.
That script requires the checkout to equal HEAD, so it runs after the
candidate commit.

## Untouched boundaries

- No PR readiness, review request, comment resolution, or PR merge.
- No credentials, live databases, checkouts, or proof stores outside
  this Git-only packet.
- No workflow, protection, deployment, producer, Suite, registry, or
  x-api change.
- No activation follows from this source repair.
