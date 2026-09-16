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
- Capture time: `2026-09-16T02:20:00Z`; lifecycle-wiring repair `2026-09-16T02:30:00Z`;
  serving-loop baseline-negative closeout `2026-09-16T02:40:00Z`; stop-then-parent-loss
  serving-loop closeout `2026-09-16T03:15:00Z`; macOS CI startup-bind repair
  `2026-09-16T03:00:00Z`.
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
`worker.join` is still blocked, one owned-session drain still runs. Operator
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
  exercises `contextlib.suppress` on the already-closed listener. Mutants that
  call `shutdown()` on the serving thread, `join()` the worker before
  parent-loss drain, exit `watch_parent` when stop is set, drop the
  `contextlib` import, or restore `HTTPServer.server_bind`'s `getfqdn`
  reverse-DNS path fail those repair tests.
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
- Full `make check` and `make build` are recorded after this proof's commit.

## Tested source hashes

| File | SHA-256 |
| --- | --- |
| `tools/standalone_supervisor.py` | `b4f4d084630fbaeb9326af08c999b380e9d1dcee8b1829c6dc0f7f5adc7727b0` |
| `tools/service_entrypoint.py` | `cfa2bb24402b423a21867d3539c45969c53b04d624975e033afa29148b9fb7af` |
| `tools/review_conductor_runtime.py` | `bac13d3d9905a0ebad8f800df0e34f6fced6930a3cc5b4874ebf064d3358f38f` |
| `tools/review_conductor_userland_launcher.py` | `d05f5e5b6e17fbe6ec2b5a477066c4d47dbf119cc4f0439a5a00b9f948ac2a30` |
| `tests/test_runtime_lifecycle.py` | `ab4aa87b568be8a23ae4520911659bc21f5e5f6a1d95f16a8933e81b2582fddb` |
| `tests/test_standalone_supervisor.py` | `25167ef17348b3743c5a1ded46c7da1b719feeab2b5b8ebedfb2994d4ae828f3` |
| `tests/test_suite_activation_launcher.py` | `233da2540e80dac5b020401979cff21facaf59df5fe87d2d42ca07dfc35a036e` |
| `tests/test_launcher_transport.py` | `5933b688ea9260dfa44403ea024b540e13c365632f33f6a36705e385503d400f` |
| `Makefile` | `78e061fdecb5c95eaf0058a0430558f4858cb3ab396c768d00b0950baf9f6b72` |
| `docs/provenance.json` | `ca8061b52c5bf530b6e4a735ddb8669fb03c52264e6b17277612ba6965ef61cb` |
| `docs/integration-contract.md` | `09e1cb0838b71d5bef2d4ade5adaae9ee81fcbcafa96030fafb37f0f976cd9a9` |
| `docs/migration.md` | `35b4a01fc6e4a3686aac29aad8a8bea039215ad917fffd31225c49e55d2a83e8` |
| `SECURITY.md` | `3d3dab7b4919eb17499d90bb205984f9430528bf6ca65547100d95c4193f0619` |

## Limits

- Synthetic processes, fake credential descriptors, and temporary files only.
  No production process was signaled.
- This is source qualification, not proof that a launchd-hosted consumer is
  installed or that live GitHub ingress survives native-thread idle retirement.
- Exact OS signal from the recorded incident remains unknown; the repair covers
  parent disappearance including SIGKILL of the supervisor write-end holder.
- Conductor15 publication work and Suite6 landing/source were not folded in.
