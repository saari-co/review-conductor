# Issue #687 PR #20 restack onto repaired core 7ee4184 — 2026-09-18

## Ownership

- Repository: `saari-co/review-conductor`.
- Isolated worktree:
  `/Users/cp-1/.openclaw/worktrees/0dbf576623060113/review-conductor-issue-687-routing-v1`
- Stacked branch: `openclaw/review-conductor-issue-687-notification-adapter-v1`
- Frozen integration base `origin/main`:
  `b16e0c8bc3f32e997db31caa173b846b0e9041ce`
- Verified pre-merge adapter head: `d3362b5a5b1acc0934c5e9f9127cbc8ffb209112`
- Ordinary merge of repaired PR #18 core:
  `7ee4184b5bb6cbf4082055d08cdb0105c2688fb4`
  (`openclaw/review-conductor-issue-687-routing-v1`).
- Merge commit that made that core an ancestor:
  `596d8bbbfaaac9f32bc5fbe2a52f9419c64d8330`
- Sole source-changing owner: this Cursor ACP worktree. No x-api, Suite,
  or other repository was edited.
- Mode: owner-authorized restack of draft PR #20 after the PR #18
  ready-tuple repair. Review ledger remains exhausted at 2/2 and is not
  reset. No rebase, amend, force-push, history rewrite, Copilot, OpenClaw,
  or ClawSweeper review request. No merge of PR #20, deploy, activation,
  credential, protection, live notification, readiness flip, or
  target-repository onboarding.

A commit cannot contain its own SHA. `candidate-manifest.json` records the
frozen main base, the merge that absorbed repaired core `7ee4184`, and
per-file hashes for this restack. The branch head after the ordinary
proof commit is the exact candidate SHA for hosted CI.

Historical packets remain exact-head truthful:
`proof/issue-687-notification-adapter-post-cap-repair-20260918/` remains
the hold_send pin-reuse repair on core `009279c`;
`proof/issue-687-notification-adapter-merge-forward-20260918/` remains
the explanation of merging core `2e40ffd` / main `b16e0c8`.

## Why this restack exists

PR #18 tip moved from `009279c` to `7ee4184`. Adapter `d3362b5` still
had the older core as ancestor. This ordinary merge makes the repaired
core an ancestor and resolves the one `docs/migration.md` conflict by
keeping adapter runtime/lease/queue prose and the new ready-tuple
fail-closed wording. No adapter runtime source was edited beyond that
required conflict resolution.

## Preserved adapter behavior

- `65b10aff` runtime restore of live queue, delivery, `run_tick`,
  `run_service_tick`, `service_entrypoint.registry_provider`, send
  lease, and notifier-transport authority.
- Copilot `5243987118` repairs: malformed persisted rail/state rows notify
  blocked once; complete current event identity; retry-attempt revalidation.
- Nested/overlapping `hold_send` pin reuse from `d3362b5`.
- Current-main PR #21 rereview behavior.
- Safe deferrals from Copilot `5247753159` are intentionally unrestored:
  repeated JSON decoding in `pending_review_notification_still_eligible`,
  and the misleading `_resolve_registry_source` lease diagnostic wording.

## Local commands and results

All commands ran in this worktree against the restacked adapter candidate
source. Fixture PASS is not a deployed review PASS.

| Command | Result |
| --- | --- |
| `git merge-base --is-ancestor 7ee4184 HEAD` | true |
| `python3 tests/test_orchestration_outcome.py` | 39 tests OK, including the two new core negatives and 36 precise mutants |
| `python3 tests/test_review_conductor_userland.py` | 43 passed |
| `AdmissionIngressTests` nested/pinned `hold_send` | ok |
| `python3 -m compileall -q tools tests scripts` | passed |
| worktree/index `git diff --check` | clean |
| `python3 scripts/check_provenance.py` | `extraction hashes verified (14 files); Python compilation passed` |
| `make check` | passed: hygiene/CODEOWNERS; conductor/activation custom suites; rereview; userland `(43)`; orchestration `(39)`; cold hydration `(24)`; projection `(25)`; presentation `30`; original-report `31`; profiles `31`; scaffold `6`; trusted admission `23`; service runtime `95`; guard `5`; launcher transport `10`; suite launcher `20`; standalone supervisor `36`; runtime lifecycle `30`; workflow contract `5`; provenance `14 files`; `py_compile`; `git diff --check`. No checks skipped. |
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
- No activation follows from this source restack.
- Copilot `5247753159` deferrals were not repaired.
