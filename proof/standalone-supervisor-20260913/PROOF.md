# Standalone SMCBD supervisor proof — 2026-09-13

## Identity and authority

- Repository: `saari-co/review-conductor`.
- Immutable starting/base commit:
  `9acb1ee3300490aa2f31ffd56b3999552834803b`.
- Branch: `openclaw/review-conductor-standalone-launcher`.
- Captured: `2026-09-13T09:26:36-0400`.
- Mutation authority: local source-only implementation and commit.
- Not authorized: push, PR creation, merge, deployment, service/tunnel startup,
  credentials, GitHub App/webhook/subscription/settings changes, branch
  protection, required checks, x-api, the legacy live Dinkus service, or either
  target repository.

## Qualified boundary

`tools/standalone_supervisor.py` is a standard-library-only foreground
supervisor for `tools/service_entrypoint.py` and one exact SMCBD profile. The
child argv contains only the profile/registry paths and fixed `--apply serve`
words. The webhook secret and GitHub App key enter the supervisor as distinct
inherited descriptors, are copied into verified unlinked mode-0600 anonymous
regular files bounded to 1 MiB, and reach the child only through explicit
`pass_fds` plus descriptor-number environment values. Incoming descriptors are
closed after preparation; retained anonymous descriptors are rewound for every
explicit start and closed on exit.

The supervisor holds a per-state-root lock and a mode-0600 Unix control socket.
Every health/stop/restart request carries a digest of the exact profile and
registry paths, profile/repository/App/installation identities,
state/checkout/proof roots, and loopback host/port. Identity mismatch is rejected
before lifecycle mutation. The service is not automatically restarted after a
crash: health reports `failed` until an explicit restart. Startup failure,
normal stop and exceptional exit remove the socket, close descriptors and bound
termination of the service-owned process group independently of leader state.
Each generation inherits distinct leader and lifetime descriptors alongside its
credential descriptors. The entrypoint makes the leader descriptor
close-on-exec, so the supervisor observes leader exit without reaping on every
supported host, including macOS without `waitid`. Every direct adapter command
uses the shared generation-bound runner, which preserves the generation
descriptor and selector through `close_fds`. The supervisor signals the numeric
PGID only while the unreaped leader and generation descriptor retain that
identity. Generation EOF precedes leader reap, and no numeric group probe or
signal follows identity release. Missing, escaped or undrained generations fail
closed.

The entrypoint compares the supervisor's canonical normalized-profile digest to
its one loaded configuration before registry or state access. Normal and
unexpected control-loop exits use the same retained cleanup state: the original
supervisor keeps its lock and control socket until its generation is gone. The
socket is mode 0600 from bind under a temporary restrictive umask. Startup
connection failures are not reported as stopped while the lock is held.

## Direct tests and mutants

The focused suite passed 32 direct tests. It exercised:

- maximum-size descriptor transport through a real inherited `/dev/fd`
  consumer before any service reader;
- descriptor rewind and byte identity across two explicit starts;
- secret absence from child argv/environment and resolver-state exclusion;
- partial credential preparation, initial spawn failure and lifecycle cleanup;
- exact config/state/checkout/proof/port identity changes;
- health, stop and explicit restart behavior;
- crash persistence without automatic respawn;
- wrong-identity rejection before stop/restart.
- rejection of group/world-writable state roots and symlinked lock files.
- bounded control framing when a request arrives in multiple stream reads.
- cleanup when acquisition fails after only the first source descriptor;
- full validated-profile identity changes, including credential selectors and
  enrollment state;
- control timeout/failure classification and response-status/identity verification;
- signal-handler installation before child spawn and restoration after failure.
- isolated process-session creation and process-group shutdown on stop/restart.
- shutdown of a SIGTERM-ignoring descendant after the service leader has exited,
  so restart cannot overlap generations.
- reaping an unreaped leader so Linux zombies cannot stall group probes;
- fail-closed restart when a process group survives SIGKILL;
- stop/restart control timeout longer than the maximum group-shutdown bound;
- rejection of a non-string control command without stopping the supervisor;
- control-socket unlink when chmod fails after bind.
- private control-socket mode from bind and restoration of the caller's umask;
- retained control/lock ownership and a rejected stop response when shutdown
  fails, followed by a successful retry without generation overlap;
- startup `ENOENT`/`ECONNREFUSED` classification while the supervisor lock is held;
- bounded retry when a startup-status probe briefly wins the lock race;
- health-based lifecycle readiness instead of socket-path existence;
- a readiness pipe proving the descendant installed `SIG_IGN` before shutdown.
- a race-free generation descriptor retained through leader exit and closed only
  after credential-bearing descendants exit, with no numeric group signal after
  generation identity release;
- non-reaping leader status while a generation remains open, including the
  portable no-`waitid` fallback used by hosted macOS;
- exact normalized-profile verification before registry or state access; and
- retained lock/control ownership when unexpected loop failure and shutdown
  failure occur together.
- portable crash reporting through a close-on-exec leader descriptor without
  reaping the process-group leader; and
- real adapter subprocess inheritance of the generation descriptor through an
  otherwise allowlisted environment and default `close_fds`.

Thirty-five disposable-copy mutants were killed by one named test each:

1. omit credential descriptors from `pass_fds`;
2. omit the generation descriptor from `pass_fds`;
3. omit the expected profile digest from the child environment;
4. launch the service in the supervisor's process session;
5. omit registry path from the control identity;
6. claim automatic restart;
7. bypass control identity comparison;
8. omit control-socket cleanup;
9. create the control socket under the caller's permissive umask;
10. omit credential-descriptor rewind before restart;
11. return from shutdown when the service leader has already exited;
12. omit SIGKILL of a generation that ignored SIGTERM;
13. reap the leader while its generation handle remains open;
14. return while the generation remains open after SIGKILL;
15. use the short control timeout for stop/restart;
16. membership-test a non-string control command;
17. register socket unlink only after chmod;
18. signal a numeric process group after generation identity release;
19. unwind ownership directly after an exceptional cleanup failure;
20. skip the entrypoint's normalized-profile digest comparison;
21. report stopped after connection failure while the supervisor lock is held;
22. follow a symlinked supervisor lock;
23. omit the complete validated-profile digest from control identity;
24. leak a source descriptor on partial acquisition;
25. let a disconnected control client escape the request boundary;
26. misreport a control timeout as a stopped supervisor;
27. accept an invalid control response status;
28. accept a response for a foreign control identity; and
29. spawn the child before installing stop-signal handlers; and
30. reap the leader on a host without non-reaping `waitid` support while its
    generation remains open; and
31. abort startup when a status probe briefly acquires the just-created lock.
32. omit the leader descriptor from `pass_fds`;
33. let adapter execs inherit the leader-only descriptor;
34. ignore leader-descriptor EOF on a host without `waitid`; and
35. omit the generation descriptor from the shared adapter-command boundary.

Each mutant ran with a bounded timeout and required nonzero status, `Ran 1 test`
and the intended `FAIL` or `ERROR` name.

## Verification

Locally exercised interpreter: CPython 3.14.6 on macOS.

- `make check` — PASS, including all legacy suites, 72 service-runtime tests,
  9 legacy launcher tests, 32 standalone-supervisor tests and all mutation
  harnesses.
- `make build` — PASS.
- `python3 -m compileall -q tools tests scripts` — PASS.
- `actionlint .github/workflows/ci.yml` — PASS; workflow bytes were unchanged.
- `scripts/check_provenance.py` — PASS: all 14 extracted files retain their
  versioned original/destination hashes and adaptations. The new supervisor is
  repository-native source, not an extracted-file ledger entry.
- Packaged-name and reproducibility coverage — PASS through
  `test_packaged_build_is_reproducible_and_independent`; the supervisor and
  service runtime remain excluded from the offline zipapp.
- `git diff --check` and `git diff --cached --check` — PASS.

The exact committed base/head whitespace gate is run after the local commit,
because no candidate head exists before that commit.

## Live limitations and untouched boundaries

- Synthetic credential bytes and injected/local subprocesses only.
- No genuine GitHub App request, webhook delivery, reviewer producer, rail check,
  OpenClaw/ClawSweeper dispatch, notification, registry promotion, HTTPS ingress,
  tunnel, installed service manager or host reboot behavior was exercised.
- Anonymous temporary storage may be disk-backed and is not secure erasure.
- The supervisor deliberately does not resolve 1Password or start cloudflared;
  protected resolver/service-manager wiring is a later deployment concern.
- The checked-in SMCBD profile remains inactive. No service or tunnel was started.
- The SMCBD target repository still carries manifest v1 with
  `quiet_seconds=600`; it was not edited here.
- GitHub App settings, webhooks, subscriptions, credentials, 1Password,
  Cloudflare, branch protection, required checks, x-api, the live Dinkus service,
  SMCBD and Blocks were untouched.

## Next safe action

Obtain exact-head source review of this local commit. After launcher
qualification is accepted, separately update and owner-promote the SMCBD target
manifest to scheduling v2 and record its exact commit/hash in the external
registry. Deployment, protected resolver wiring, tunnel/service startup and
shadow activation each remain separate explicit authorizations.

## Exact-head source-review follow-up

Owner approval was subsequently granted to publish this source slice for review
and begin the separate target-repository scheduling-v2 change. Exact-head review
removed one duplicated README inventory row and hardened the fail-closed
lifecycle: complete validated-profile identity, partial source-descriptor
cleanup, control-client failure containment, timeout classification, response
validation, signal installation before child spawn, and process-group shutdown.
Direct tests and precise mutants cover each edge. The complete local gate set was
rerun before publication. Draft publication does not authorize merge,
deployment, manifest-hash promotion or shadow activation.

Copilot review `5191028941` on `832f4d11659b8c118ee8e3108680f24c165e8ddf`
found that `stop_service_process()` returned when the service leader had
exited, so a SIGTERM-ignoring descendant could survive into the next
generation. Shutdown now probes and signals the process group independently of
leader state. Direct descendant-process regressions and two precise mutants
cover the early-return and omitted-SIGKILL cases. Suite PR #8 was not edited.

Copilot review `5191095466` on `3ad730156133b02ab33cf1d9f6380b827dc3857b`
required four fail-closed repairs: reap zombies during group probe, fail closed
if the group survives SIGKILL, wait longer than the shutdown bound for restart
control, reject non-string commands without stopping, and unlink the control
socket from bind before chmod. Direct regressions and precise mutants cover
each. Suite PR #8 was not edited.

Copilot review `5191138752` on `d13d13446c45b8595f9e2e947493c97271f9b6d0`
identified five remaining activation-boundary issues: private socket mode at
bind, retention of lock/control ownership after failed shutdown, deterministic
descendant readiness, health-based lifecycle readiness, and startup connection
classification while locked. This bounded engineering pass repairs those
invariants and deliberately does not request another Copilot review.

Copilot review `5191848326` on `335586a6bb5a9b5aa3ebbce7c4f2795f78bbe92e`
identified three activation-boundary issues: numeric PGID reuse after leader
reap, profile replacement between supervisor identity and child load, and lock /
socket release when an unexpected loop failure coincides with failed cleanup.
The generation-lifetime descriptor, entrypoint profile-digest gate and retained
exceptional-cleanup loop repair those exact invariants. No additional Copilot
review is requested by this repair task.

Copilot review `5192283782` on `c7bd1a3140acde9a146b459af8754b76c0ae54c9`
found that macOS could report an exited leader as running while descendants
held the generation descriptor, and that direct adapter subprocesses using
`close_fds` could escape generation drainage. A separate leader-only
close-on-exec descriptor now reports exit without reaping on every supported
host. All active adapter subprocess boundaries use one generation-preserving
runner, with direct tests and precise mutants for both invariants.
