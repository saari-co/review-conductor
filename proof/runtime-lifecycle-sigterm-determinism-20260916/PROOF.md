# Runtime lifecycle SIGTERM test determinism — 2026-09-16

## Ownership

- Repository: `saari-co/review-conductor`.
- Isolated worktree:
  `/Users/cp-1/.openclaw/worktrees/0dbf576623060113/review-conductor-openclaw-adjudication-routing-20260916`
- Branch: `openclaw/review-conductor-openclaw-adjudication-routing-20260916`
- Required start/base/head: `e5824afe876b28ce8cb74e97080b1d1b1f23482b` (`origin/main`).
- Prior PR head before this repair: `d2b6b14456261aa7f7eaa5bae38b299bd485de71`.
- Sole source-changing owner: this worktree. No other worktree was opened or
  edited, including `/Users/cp-1/Developer/review-conductor/openclaw-smcbd-suite`.
- Mode: lifecycle-test repair only. Production `request_stop` remains off-thread.
  No waiting_human adjudication, merge, deploy, activation, credential,
  protection, live-state, Suite PR #6, or review-rail request.

A commit cannot contain its own SHA. `candidate-manifest.json` records the
required start SHA and per-file source hashes. The branch head after this
commit is the exact candidate SHA for later CI and owner review.

## Confirmed inherited defect

Hosted candidate run
[35127137575](https://github.com/saari-co/review-conductor/actions/runs/35127137575)
on exact `d2b6b14` failed Python 3.11 / Ubuntu:

1. `test_precise_runtime_lifecycle_mutants` expected
   `test_entrypoint_sigterm_stops_real_serving_loop` to fail after replacing the
   helper-thread `shutdown()` with an in-thread call. The mutated subprocess
   returned 0 / `Ran 1 test OK` in 0.097s.
2. `test_baseline_actual_serving_loop_sigterm_deadlocks_on_serve_thread_shutdown`
   asserted `process.poll() is None` after `SIGTERM` plus `time.sleep(1.0)` and
   observed `0`.

Exact-base main run
[35089876605](https://github.com/saari-co/review-conductor/actions/runs/35089876605)
failed the same baseline family on Python 3.12 / Ubuntu. This is inherited
`BaseServer.shutdown` / signal-thread interleaving, not permission to weaken
production lifecycle safety.

`socketserver.BaseServer.shutdown` is documented to deadlock if invoked on the
serving thread. On hosted Ubuntu that wait can return, so a one-second poll is
not evidence.

## Correction

`ENTRYPOINT_SERVICE`'s `ReportingServer` now records explicit loop and shutdown
state:

- `serve-loop-entered` is written on the serving thread before
  `serve_forever`.
- `shutdown-called` records caller name/ident and
  `same_serving_thread=0|1`.
- Same-thread `shutdown()` writes `shutdown-on-serve-thread` and waits on an
  event that only `serve_forever`'s `finally` can set. Helper-thread shutdown
  still calls `super().shutdown()`.
- `serve-loop-left` is written when the loop actually returns.

`spawn_entrypoint_service` waits for `serve-loop-entered` before returning, so
tests do not signal during the bind-to-loop window. The baseline-negative test
waits for `shutdown-on-serve-thread` and then asserts the process, listener,
and lock remain held and that `serve-loop-left` / `server-closed` are absent.
The repaired SIGTERM/SIGHUP tests assert `same_serving_thread=0` and
`serve-loop-left`. Fixture counters use atomic replace so parent waits do not
observe a truncated integer.

Production `tools/service_entrypoint.py` is unchanged. Off-thread shutdown,
parent-loss drain, fail-closed lock/session cleanup, and waiting_human
adjudication are untouched.

## Local commands and results

All commands ran in this worktree against the candidate source. Fixture PASS
is not a deployed review PASS.

| Command | Result |
| --- | --- |
| `python3 -m unittest -v` focused SIGTERM baseline, SIGTERM/SIGHUP repair, and `test_precise_runtime_lifecycle_mutants` | `Ran 4 tests` OK |
| `python3 -m unittest discover -s tests -p 'test_runtime_lifecycle.py' -v` | `Ran 30 tests` OK |
| `make check` | passed, including lifecycle `30` tests, provenance `14 files`, `py_compile`, and `git diff --check` |
| `make build` | wrote `dist/review-conductor.pyz` |
| `python3 scripts/check_provenance.py` | `extraction hashes verified (14 files); Python compilation passed` |
| `python3 -m compileall -q tools tests scripts` | clean |
| `git diff --check` | clean |

Exact-head whitespace is `scripts/check_whitespace.py` against
`e5824afe876b28ce8cb74e97080b1d1b1f23482b` and the post-commit HEAD. That
script requires the checkout to equal HEAD, so it runs after the candidate
commit. Local interpreters available here were Python 3.14.6 and macOS
`/usr/bin/python3` 3.9.6; 3.9 is below the hosted matrix and was not used as
coverage. Hosted 3.11/3.12 remain the missing platform proof until CI.

## Tested source hashes

| File | SHA-256 |
| --- | --- |
| `tests/test_runtime_lifecycle.py` | `3a6f14fdda510223188aad0c5eb750b65b24159413169929e6d72abd6bee4a47` |
| `proof/clawsweeper-human-gate-adjudication-20260916/PROOF.md` | `18e16b625e8dd7f977cd970b67f7395c2f9739ac6ab981a137e3e8e659595b5e` |
| `tools/service_entrypoint.py` | `4524956928ae196ae7ee49313e25e2fafc8f704682d0064be72442205ddc6a85` |

`tools/service_entrypoint.py` is unchanged from the durable-runtime
qualification.

## Limits

- Synthetic fixture processes, fake credential descriptors, and temporary
  files only. No production process was signaled.
- This proves test determinism of the documented same-thread shutdown
  contract. It is not hosted Ubuntu 3.11 proof until the exact-head CI run
  completes.
- waiting_human adjudication source was not edited.
