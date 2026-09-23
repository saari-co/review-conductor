# Dispatch transport ancestor symlink repair — 2026-09-23

## Ownership and tuple

- Repository: `saari-co/review-conductor`
- Worktree: `/Users/cp-1/.openclaw/workspace/main/smoky/conductor-dispatch-repair-20260923`
- Branch: `fix/dispatch-transport-preflight-20260923`
- Base: `94b19d0246c54123fee6c5c091cc5db609b809bb`
- Starting head: `86723c7998333e0c59a0972ce8a162c2157d5fe3`
- PR: https://github.com/saari-co/review-conductor/pull/25 (draft preserved)
- Capture time: 2026-09-23T15:23:58Z
- Interpreter exercised: Python 3.14.6
- This packet cannot name its own commit SHA. The branch head after the repair commit is the exact candidate for hosted CI.

No merge, deployment, credential, protection, live-adapter, OpenClaw, or ClawSweeper change.

## Review ledger

Retained cap: two consecutive unsuccessful Copilot repair/re-review cycles. This packet does not reset that ledger.

Completed unsuccessful cycles before this repair: **1**.

| Cycle | Evidence | Disposition |
| --- | --- | --- |
| 1 | Review `5292825937` / `PRR_kwDOUXe3188AAAABO3odUQ` by `copilot-pull-request-reviewer[bot]`, submitted 2026-09-23T15:07:25Z, state `COMMENTED`, commit `86723c7998333e0c59a0972ce8a162c2157d5fe3` | Unsuccessful. One inline high finding, zero suppressed findings in the review body or review comments. |

Finding: https://github.com/saari-co/review-conductor/pull/25#discussion_r4083986552 (`4083986552`, thread `PRRT_kwDOUXe3186lNeMA`). `O_NOFOLLOW` on the full adapter path covers only the leaf. Reproduced on the starting head: when `<source>/bin` is a symlink to an external directory whose `smoky` is mode `0755` and whose single `DEST` is this checkout, `spark_transport_component` returned `ready`.

Cycle 2 is the authorized Copilot re-review after this repair is pushed and exact-head CI is terminal. It is not a completed cycle in this packet.

## Repair

`adapter_receipt_destinations` opens the trusted `source_root` and each relative ancestor with `O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC`, then opens the leaf from that directory with `O_RDONLY|O_NOFOLLOW|O_NONBLOCK|O_CLOEXEC`. A symlinked ancestor fails closed as not a regular file. The same checkout-bound executable reached through a real directory remains ready. The check still does not execute the adapter, rewrite `DEST`, or select another checkout.

## Local checks

- `python3 -m unittest` of the existing transport health test, `test_health_rejects_symlinked_spark_transport_ancestor`, and `test_following_transport_ancestor_mutant_is_rejected`: 3 tests, OK.
- `make check`: passed. Includes governance, integration, rereview, activation, userland `(43)`, orchestration `(37)`, cold hydration `(24)`, projection `(25)`, presentation `(30)`, original report `(31)`, profiles `(35)`, scaffold `(6)`, trusted admission `(23)`, service runtime `(96)`, guard `(5)`, launcher transport `(10)`, suite launcher `(20)`, standalone supervisor `(36)`, runtime lifecycle `(30)`, workflow contract `(5)`, provenance `extraction hashes verified (14 files)`, Python compilation, and `git diff --check`. No checks skipped.
- `make build`: wrote `dist/review-conductor.pyz`.
- Mutant: disposable copy replaces the unique ancestor open `ancestor = os.open(component, directory_flags, dir_fd=held)` with `directory_flags & ~os.O_NOFOLLOW`. The ancestor regression exits nonzero with `Ran 1 test` and `FAIL: test_health_rejects_symlinked_spark_transport_ancestor`.

Exact base/head whitespace is `python3 scripts/check_whitespace.py 94b19d0246c54123fee6c5c091cc5db609b809bb <repair-head>` after the commit, because that checker requires checkout `HEAD` to be the event head.

## Untouched boundaries

Profile parsing, dispatch execution, supervisor lifecycle, credentials, enrollment, branch protection, and the restored live adapter are unchanged. Health still withholds readiness; it does not stop an already-started worker by itself. Hosted CI and the cycle-2 Copilot review are not claimed by this local packet.
