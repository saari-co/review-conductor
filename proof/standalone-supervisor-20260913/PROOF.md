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
child termination through terminate/kill/reap.

## Direct tests and mutants

The focused suite passed 16 tests. It exercised:

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
- control timeout/failure classification and response-identity verification;
- signal-handler installation before child spawn and restoration after failure.

Thirteen disposable-copy mutants were killed by one named test each:

1. omit `pass_fds`;
2. omit registry path from the control identity;
3. claim automatic restart;
4. bypass control identity comparison;
5. omit control-socket cleanup;
6. omit descriptor rewind before restart.
7. follow a symlinked supervisor lock.
8. omit the complete validated-profile digest from control identity;
9. leak a source descriptor on partial acquisition;
10. let a disconnected control client escape the request boundary;
11. misreport a control timeout as a stopped supervisor;
12. accept a response for a foreign control identity;
13. spawn the child before installing stop-signal handlers.

Each mutant ran with a bounded timeout and required nonzero status, `Ran 1 test`
and the intended `FAIL` or `ERROR` name.

## Verification

Locally exercised interpreter: CPython 3.14.6 on macOS.

- `make check` — PASS, including all legacy suites, 72 service-runtime tests,
  9 legacy launcher tests, 16 standalone-supervisor tests and all mutation
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
removed one duplicated README inventory row and hardened six fail-closed edges:
complete validated-profile identity, partial source-descriptor cleanup,
control-client failure containment, timeout classification, response identity,
and signal installation before child spawn. Direct tests and precise mutants
cover each edge. The complete local gate set was rerun before publication. Draft
publication does not authorize merge, deployment, manifest-hash promotion or
shadow activation.
