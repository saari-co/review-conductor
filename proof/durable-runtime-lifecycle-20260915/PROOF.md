# Durable standalone runtime lifecycle — source qualification

## Assignment and source

- Repository: `saari-co/review-conductor`.
- Sole source owner: this isolated worktree on
  `codex/durable-suite-runtime-20260915`.
- Fresh fetched base: `8cefc3548d40b3868263cacd54a9568d51fbe67f`.
- Mode: source mutation only. Root owns runtime activation, final review, and
  merge. This patch is not deployed and does not change credentials, launchd,
  profiles, or Suite6 source.
- Capture time: `2026-09-16T02:06:32Z`.
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
Standalone outer `stop_child` waits
`STANDALONE_CHILD_STOP_SECONDS` (22), matching
`SHUTDOWN_CONTROL_SECONDS`, so the launcher cannot SIGKILL the supervisor
before the 10s TERM plus 10s KILL drain. Legacy Blocks `start` keeps the
ten-second budget. Credentials remain descriptor-only; no values are logged.

No new orchestration framework, credential consumer, hosted dependency, or
production unit is added. A durable launchd host remains a separate root-owned
activation.

## Verification

- Baseline-negative actual-process test: a sessioned fake service that does not
  watch the parent descriptor remains listening and holds its lock after the
  parent write end closes.
- Repair actual-process tests: graceful parent-write close and abrupt owner
  SIGKILL drain the listener, lock, and session, including a TERM-resistant
  fake child. Synthetic credentials and temporary files only.
- Partial parent-pipe setup closes already-created descriptors and does not
  spawn. Missing or closed parent descriptors fail before registry access.
- Drain refuses to signal when the process is not the owned session leader.
- Existing standalone supervisor suite (36 tests) and suite activation launcher
  suite (20 tests) remain PASS, including their mutant batteries.
- New `tests/test_runtime_lifecycle.py` is registered in `Makefile` `test`.
- Full `make check` and `make build` are recorded after this proof's commit.

## Tested source hashes

| File | SHA-256 |
| --- | --- |
| `tools/standalone_supervisor.py` | `b4f4d084630fbaeb9326af08c999b380e9d1dcee8b1829c6dc0f7f5adc7727b0` |
| `tools/service_entrypoint.py` | `866dfed1727f56be6d9db4a61bf8975dc80e1e24af43e563ec7717ee599ae632` |
| `tools/review_conductor_userland_launcher.py` | `398a6615e8c5c0957aeacc415d08b1e916998a0a18aa3c08e2bf41dd25a57616` |
| `tests/test_runtime_lifecycle.py` | `69214f23e5a8e4d3f3e49bedc671c35056c82a29bf4803acda601f1b3e1b976b` |
| `tests/test_standalone_supervisor.py` | `25167ef17348b3743c5a1ded46c7da1b719feeab2b5b8ebedfb2994d4ae828f3` |
| `tests/test_suite_activation_launcher.py` | `0f4b838dd0c3c11e68ce01f8bde5d6239846ccfff268c3849c43b28938df3d1a` |
| `Makefile` | `78e061fdecb5c95eaf0058a0430558f4858cb3ab396c768d00b0950baf9f6b72` |
| `docs/provenance.json` | `818f848e013f1eff833c39e7532bdf1e99387927f49adab41697b4292dcb189d` |

## Limits

- Synthetic processes, fake credential descriptors, and temporary files only.
  No production process was signaled.
- This is source qualification, not proof that a launchd-hosted consumer is
  installed or that live GitHub ingress survives native-thread idle retirement.
- Exact OS signal from the recorded incident remains unknown; the repair covers
  parent disappearance including SIGKILL of the supervisor write-end holder.
- Conductor15 publication work and Suite6 landing/source were not folded in.
