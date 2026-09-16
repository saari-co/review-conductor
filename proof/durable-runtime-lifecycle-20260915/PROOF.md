# Durable standalone runtime lifecycle — source qualification

## Assignment and source

- Repository: `saari-co/review-conductor`.
- Sole source owner: this isolated worktree on
  `codex/durable-suite-runtime-20260915`.
- Fresh fetched base: `8cefc3548d40b3868263cacd54a9568d51fbe67f`.
- Mode: source mutation only. Root owns runtime activation, final review, and
  merge. This patch is not deployed and does not change credentials,
  profiles, Suite6 source, or existing consumer credential/resolver/profile
  paths and values. No real launchd unit is stored in Git.
- Capture times are committer/CI evidence, not rounded estimates:
  first publication commit `d7b1ee7` `2026-09-16T02:09:38Z`; child-stop helper
  `26674af` `2026-09-16T02:19:30Z`; off-thread shutdown wiring `cffef22`
  `2026-09-16T02:27:38Z`; serving-loop baseline-negative `74f39dc`
  `2026-09-16T02:32:30Z`; stop-then-parent-loss and no-reverse-DNS bind
  `cd2543f` `2026-09-16T02:54:03Z`; hosted run 35049760234 macOS job
  completed `2026-09-16T02:56:45Z`, aggregate CI `2026-09-16T02:56:51Z`;
  Copilot review 5218102918 submitted `2026-09-16T03:06:37Z` on exact
  `cd2543f`; round-1 repair local lifecycle closeout `2026-09-16T10:42:23Z`;
  local `make check` and `make build` `2026-09-16T10:44:36Z`; Copilot review
  5221776785 submitted `2026-09-16T10:59:51Z` on exact `ef9f625`; final
  post-review repair local `make check` and `make build` `2026-09-16T11:12:54Z`.
- Local interpreter exercised: Python 3.14.6.

## Confirmed defect and correction

An always-on standalone service starts in its own process session while the
caller, launcher, supervisor, and tunnel share the outer task group. Existing
lifetime pipes only watched child/generation exit. When the outer owner
disappeared, the isolated service kept its listener and operation lock.

This source patch adds a reciprocal parent-lifetime descriptor: the supervisor
holds the write end and the service watches the read end. EOF on that
descriptor fails closed and drains the still-owned service session. The service
signals only its current session/group while it remains the leader; it does not
probe released PGIDs. Standalone launcher/supervisor also handle SIGHUP.
Standalone outer stop uses `stop_standalone_child`, which waits
`STANDALONE_CHILD_STOP_SECONDS` (22), matching
`SHUTDOWN_CONTROL_SECONDS`, so the launcher cannot SIGKILL the supervisor
before the 10s TERM plus 10s KILL drain. Legacy Blocks `start` keeps
`stop_child`'s ten-second default for both the conductor and tunnel. Credentials
remain descriptor-only; no values are logged.

Entrypoint SIGINT/SIGTERM/SIGHUP only set the stop event and schedule
`serve_forever` shutdown on a helper thread. `socketserver.BaseServer.shutdown`
cannot run in the serving/signal thread. Parent-loss closes the listener,
refuses further worker ticks, and bounded-drains the owned session without
joining an in-flight worker. Parent-liveness supervision continues after stop
is requested until shutdown completes; if the supervisor write end closes while
`worker.join` is still blocked, the owned-session drain still runs. That drain
observes remaining members of the owned session without `waitpid(-1)`, so a
concurrent review-worker `subprocess.run` keeps its real exit status; drain
completion is recorded only after `drain_owned_session()` succeeds, and the
parent watcher retries a signaling error while join remains blocked. Final
service exit waits for that drain after `worker.join` returns, so parent EOF
during a blocked join cannot let main return while the daemon watcher is still
draining. Repeated parent-loss retries schedule the HTTP shutdown helper once
and retain only the first signaling failure. Operator
stop still waits unbounded for that non-daemon worker. Ingress bind records the
listen host and port without calling `socket.getfqdn()`; hosted macOS CI
35048390624 stalled every actual entrypoint fixture in `HTTPServer.server_bind`
before `ReportingServer` could write ready, with empty stderr and leaked child
groups because cleanup was registered only after readiness.

No new orchestration framework, credential consumer, hosted dependency, service
manager, or production unit is added. A durable launchd host remains a separate
root-owned activation.

## Root caller adaptation after merge

Do not edit this repository's consumer or invent a service manager. The
installed fixed consumer stays root-owned. After this lands, change only the
one conductor/supervisor stop call:

```text
- launcher.stop_child(conductor)
+ launcher.stop_standalone_child(conductor)
```

Leave the tunnel (and any other Blocks child) on `launcher.stop_child(...)`.
`stop_child` without a timeout remains 10 seconds. `stop_standalone_child`
uses 22 seconds (`STANDALONE_CHILD_STOP_SECONDS`). Worst-case outer reap is
TERM wait plus KILL wait, `2 * 22 = 44` seconds.

Root prepares the external launchd unit with KeepAlive=false (no automatic
retry), RunAtLoad=true, AbandonProcessGroup=false, and ExitTimeOut longer than
that complete standalone stack drain. Existing consumer credential, resolver,
and profile paths and values stay unchanged.

## Verification

- Baseline-negative actual-process test: a sessioned fake service that does not
  watch the parent descriptor remains listening and holds its lock after the
  parent write end closes.
- Baseline-negative actual serving-loop tests inject the same synthetic
  client/registry, real loopback `BoundedHTTPServer`, and state-root lock, then
  restore each unfixed shutdown path in a disposable tools copy. SIGTERM with
  `shutdown()` on the serving thread leaves the process, listener, and lock
  held. Parent-loss that `join()`s the worker before drain, and also omits the
  watcher-owned drain, leaves the process, lock, and session held while the
  blocked tick does not finish. A watcher that exits when `stop` is set leaves
  the process, lock, and session held after blocked tick, graceful stop, then
  parent write-end close.
- Repair actual-process tests: graceful parent-write close and abrupt owner
  SIGKILL drain the listener, lock, and session, including a TERM-resistant
  fake child. Synthetic credentials and temporary files only.
- Repair actual serving-loop tests: SIGTERM and SIGHUP stop that loop without
  deadlock. Parent-loss with a blocked worker plus TERM-resistant descendant
  still drains the listener, lock, and session; the blocked tick does not
  finish. The same loop with a blocked tick, then SIGTERM, then parent
  write-end close still drains listener, lock, and generation within bound and
  exercises `contextlib.suppress` on the already-closed listener. A first
  signaling error during that blocked-join drain is retried and then completes;
  restoring `session_drained` before a successful drain leaves the process,
  lock, and session held after one failed attempt. Removing the final exit
  fence lets a gated worker return during a slow in-flight drain so the
  process exits while a TERM-resistant descendant remains. Repeated drain
  failures without one-shot helper scheduling start six or more shutdown
  threads; the repaired path keeps exactly one helper and still retries
  fail-closed. Fixture helpers own `parent_write` before `Popen`, so a spawn
  failure closes that writer without a later close of a reused descriptor.
  Mutants that
  call `shutdown()` on the serving thread, `join()` the worker before
  parent-loss drain, exit `watch_parent` when stop is set, drop the
  `contextlib` import, restore `HTTPServer.server_bind`'s `getfqdn`
  reverse-DNS path, restore `waitpid(-1)` observation, mark drain complete
  before success, omit the final exit fence, start a shutdown helper on every
  retry, append every signaling failure, or register `parent_write` only after
  `Popen` fail those repair tests. Python `os.waitid(..., WNOHANG)` returning
  `None` when no child matches remains the production contract.
- Baseline-negative child-reap test: concurrent `waitpid(-1, WNOHANG)` during
  owned-session drain steals a session-leader adapter child's status so
  `Popen.wait()` reports `0` instead of `17`. The repaired non-reaping observer
  leaves `17` intact and does not call `waitpid(-1)`.
- Actual entrypoint fixtures write child startup-stage files and register
  session/fd cleanup immediately after `Popen`, so a readiness timeout names
  the last stage and cannot leak the child group.
- Partial parent-pipe setup closes already-created descriptors and does not
  spawn. Missing or closed parent descriptors fail before registry access.
- Drain refuses to signal when the process is not the owned session leader.
- `stop_child()` records the legacy 10s budget; `stop_standalone_child()`
  records 22s; legacy `start` still uses `stop_child` for conductor and tunnel.
- Existing standalone supervisor suite and suite activation launcher suite
  remain PASS, including their mutant batteries. A mutant that routes
  standalone stop back through bare `stop_child` fails.
- New `tests/test_runtime_lifecycle.py` is registered in `Makefile` `test`.
- Full `make check` and `make build` passed at `2026-09-16T11:12:54Z` on
  Python 3.14.6 before this proof's commit. Historical round-1 closeout was
  `2026-09-16T10:44:36Z` on `ef9f625`.

## Tested source hashes

| File | SHA-256 |
| --- | --- |
| `tools/standalone_supervisor.py` | `b4f4d084630fbaeb9326af08c999b380e9d1dcee8b1829c6dc0f7f5adc7727b0` |
| `tools/service_entrypoint.py` | `4524956928ae196ae7ee49313e25e2fafc8f704682d0064be72442205ddc6a85` |
| `tools/review_conductor_runtime.py` | `bac13d3d9905a0ebad8f800df0e34f6fced6930a3cc5b4874ebf064d3358f38f` |
| `tools/review_conductor_userland_launcher.py` | `d05f5e5b6e17fbe6ec2b5a477066c4d47dbf119cc4f0439a5a00b9f948ac2a30` |
| `tests/test_runtime_lifecycle.py` | `11b494ef590dd8b27815dbbf00f4d41e8ab4958cedd00fc977f70f2055959f75` |
| `tests/test_standalone_supervisor.py` | `25167ef17348b3743c5a1ded46c7da1b719feeab2b5b8ebedfb2994d4ae828f3` |
| `tests/test_suite_activation_launcher.py` | `233da2540e80dac5b020401979cff21facaf59df5fe87d2d42ca07dfc35a036e` |
| `tests/test_launcher_transport.py` | `5933b688ea9260dfa44403ea024b540e13c365632f33f6a36705e385503d400f` |
| `Makefile` | `78e061fdecb5c95eaf0058a0430558f4858cb3ab396c768d00b0950baf9f6b72` |
| `docs/provenance.json` | `ca8061b52c5bf530b6e4a735ddb8669fb03c52264e6b17277612ba6965ef61cb` |
| `docs/integration-contract.md` | `874276c7fea2fcbd6cbfb2330a701696e3f822f4f384db37bf462bda05f82d0a` |
| `docs/migration.md` | `f90d089bdb41fa5ba29cd18909c2a2a3d9ab91f72e73845c9b5ce8bf629e3507` |
| `SECURITY.md` | `3e17dda88af678ebb8d2cb1fd504026a5fa631bdcbb2cd1b5463c984cbc312ed` |

## Limits

- Synthetic processes, fake credential descriptors, and temporary files only.
  No production process was signaled.
- This is source qualification, not proof that a launchd-hosted consumer is
  installed or that live GitHub ingress survives native-thread idle retirement.
- Exact OS signal from the recorded incident remains unknown; the repair covers
  parent disappearance including SIGKILL of the supervisor write-end holder.
- Conductor15 publication work and Suite6 landing/source were not folded in.
