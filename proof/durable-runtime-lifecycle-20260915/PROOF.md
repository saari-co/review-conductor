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
- Capture time: `2026-09-16T02:20:00Z`; lifecycle-wiring repair `2026-09-16T02:30:00Z`.
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
joining an in-flight worker. Operator stop still waits unbounded for that
non-daemon worker.

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
- Repair actual-process tests: graceful parent-write close and abrupt owner
  SIGKILL drain the listener, lock, and session, including a TERM-resistant
  fake child. Synthetic credentials and temporary files only.
- Actual entrypoint serving-loop tests inject a synthetic client/registry,
  bind a real loopback `BoundedHTTPServer`, and take the real state-root lock.
  SIGTERM and SIGHUP stop that loop without deadlock. Parent-loss with a
  blocked worker plus TERM-resistant descendant still drains the listener,
  lock, and session; the blocked tick does not finish. Mutants that call
  `shutdown()` on the serving thread or `join()` the worker before parent-loss
  drain fail those tests.
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
| `tools/service_entrypoint.py` | `4c71010307d5e27784977b416ea8d38068ea95f1ecdb65ba91957311e75bbe5d` |
| `tools/review_conductor_userland_launcher.py` | `d05f5e5b6e17fbe6ec2b5a477066c4d47dbf119cc4f0439a5a00b9f948ac2a30` |
| `tests/test_runtime_lifecycle.py` | `97f701fded6babc8194659dd75139c01afbfda1ea80d238ec0702dba774d0a0a` |
| `tests/test_standalone_supervisor.py` | `25167ef17348b3743c5a1ded46c7da1b719feeab2b5b8ebedfb2994d4ae828f3` |
| `tests/test_suite_activation_launcher.py` | `233da2540e80dac5b020401979cff21facaf59df5fe87d2d42ca07dfc35a036e` |
| `tests/test_launcher_transport.py` | `5933b688ea9260dfa44403ea024b540e13c365632f33f6a36705e385503d400f` |
| `Makefile` | `78e061fdecb5c95eaf0058a0430558f4858cb3ab396c768d00b0950baf9f6b72` |
| `docs/provenance.json` | `36551a5c73e561477bd99f36fd4ef340e9f9d9d045664db9a98a19ae91b5c438` |
| `docs/integration-contract.md` | `f24b89db7357d2e72d8928dce9ac7dabdf1188beacdbdb1a889234f8d07f30c5` |
| `SECURITY.md` | `a7731f8e81b3444200c20c99947d393da7621125645d9ca642b7c888b141cd60` |

## Limits

- Synthetic processes, fake credential descriptors, and temporary files only.
  No production process was signaled.
- This is source qualification, not proof that a launchd-hosted consumer is
  installed or that live GitHub ingress survives native-thread idle retirement.
- Exact OS signal from the recorded incident remains unknown; the repair covers
  parent disappearance including SIGKILL of the supervisor write-end holder.
- Conductor15 publication work and Suite6 landing/source were not folded in.
