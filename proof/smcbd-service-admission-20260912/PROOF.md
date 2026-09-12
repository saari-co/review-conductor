# SMCBD service admission and App adapter source proof — 2026-09-12

## Identity and authority

- Repository: `saari-co/review-conductor`.
- Base: protected `main` at `320534450bbfe924bf855b762a0f9358f0b40336`
  (merged PR #2, trusted enrollment and approved-policy admission).
- Branch: `openclaw/review-conductor-pr3-service-webhook`, opened as draft PR #3.
- Candidate source slice: local commit
  `8e90d46b9287f37382dba1eb5569b0f824a28084` on the local-only branch
  `openclaw/review-conductor-trusted-admission-fable`. It was written against
  the pre-squash PR #2 history, so it was ported and repaired rather than
  replayed; PR #1/#2 commits were not re-applied. The earlier proof produced on
  that candidate branch is historical and its PASS is not carried forward.
- Mutation owner: this porting task. Authorization covered local source
  implementation, verification, one new branch push and one draft PR. It did
  not cover App settings, credential generation, webhook enabling, tunnel or
  deployment changes, target-repository or branch-protection changes, check
  rebinding, merge, release or bootstrap adjudication.
- `AGENTS.md`, `CONTRIBUTING.md`, `SECURITY.md`, the architecture, integration,
  migration, bootstrap and trusted-admission contracts, and the PR #2 proof
  were read before porting.

## Port and repairs against current main

- `tests/test_trusted_admission.py` conflicted: main's PR #2 squash added
  `manifest_bytes` retention, forged derived-field rejection and policy-reader
  failure normalization that the candidate lacked. The merged suite keeps every
  PR #2 case and adds App-ID binding cases (18 tests).
- `tools/trusted_admission.py` merged cleanly: `Enrollment`, `Registry.lookup`,
  `Admission`, `admit` and `policy_is_current` now bind the GitHub App ID as well
  as repository name/numeric ID and installation; the registry document uses a
  `github_app` block. The library remains inert.
- Repair: `tools/service_entrypoint.py` let `AdmissionError` escape the profile
  enrollment lookup, so a non-enrolled profile crashed with a traceback instead
  of exiting 2. It now normalizes to `ServiceError`, refuses a registry with no
  enrollments, and prints through `sys.stderr`.
- Repair: the candidate's authentication-order test could not detect a signature
  bypass (its malformed body failed preflight anyway). It now sends an enrolled,
  well-formed body with forged/missing/malformed signatures against a registry
  spy and a failing policy reader.
- Added coverage: configured App/installation/repository ID versus registry
  mismatch; post-promotion redelivery of the same exact tuple is refused and
  rolled back (one tuple keeps one binding); real bounded loopback HTTP matrix
  (valid, replay, delivery-ID reuse with different content, forged/unsigned,
  cross-installation, cross-repository, malformed JSON, unsafe delivery ID,
  wrong event/path/content type, stale head after supersession); registry file
  mode/symlink/relative/directory/missing cases; the shipped inactive candidate
  profile is refused by the entrypoint before the registry or credentials are
  touched; an enabled profile still fails before credentials when the registry
  disagrees; `--apply`, `--profile` and `--registry` are mandatory.
- Docs: README profile-fixture row; trusted-admission single-binding semantics.

## Source behavior

- `service_runtime.py` verifies HMAC before JSON parsing, enrollment lookup or
  policy I/O; rejects unknown App/installation/repository name/numeric ID
  combinations and profile/registry disagreement; admits the approved
  `.review-conductor.json` at the pinned commit for every accepted tuple; and
  persists the binding inside the engine's `BEGIN IMMEDIATE` delivery
  transaction so a failed binding rolls back the delivery.
- Worker/check projection is blocked while any live head lacks a current
  binding. Duplicate delivery is idempotent; content reuse fails closed.
- `GitHubAppClient` mints installation tokens for the exact repository and the
  profile's permission map (Contents read-only for standalone profiles), allows
  only the approved policy path at a 40-hex commit, publishes only the two fixed
  checks, and exposes no merge, protection or foreign-installation endpoint;
  forbidden operations reach no transport call.
- `service_entrypoint.py` requires `--apply`, an enabled profile, a same-user
  absolute regular registry file without group/world bits, profile/registry
  agreement, and descriptor-delivered credentials. The inactive SMCBD candidate
  profile keeps `enrollment.enabled: false` with explicit blockers.
- The Blocks legacy permission map, denied list, CLI route and profile bytes
  are unchanged.

## Verification at this head (local CPython 3.14.6, macOS arm64)

- `make check` — PASS: legacy engine/activation/userland/profile suites,
  scaffold and repository guards, trusted admission (18), service runtime (16,
  including the mutation harness), launcher transport, workflow contract,
  extraction provenance (14 files) and Python compilation, `git diff --check`.
- `make build` — PASS; the packaged zipapp still excludes service, admission
  and runtime modules.
- `python3 -m compileall -q tools tests scripts` — PASS.

Executable disposable-copy mutants (`MutationTests`, bounded subprocess, each
must fail its named test and only that test):

1. bypass HMAC verification before parsing;
2. ignore the configured App identity;
3. admit a foreign installation;
4. rebind an exact tuple to a conflicting policy;
5. bypass current-policy binding enforcement before worker projection;
6. request Contents write instead of read-only;
7. allow reading any repository content path;
8. skip the service enrollment check in the entrypoint;
9. serve an inactive profile;
10. accept a group-readable registry.

Only CPython 3.14 was exercised locally; 3.11/3.12 evidence comes from hosted
exact-head CI on the PR, recorded in the PR conversation, not here.

## Not exercised / live limitations

- No genuine App-owned check, OpenClaw review or ClawSweeper review ran.
- No live GitHub API, registry file, credential, tunnel, HTTPS edge or webhook
  delivery was used; transports are injected and identities are synthetic
  except the non-secret App/installation/repository IDs pinned in the profile.
- The proposed hostname/tunnel name in the candidate profile is a proposal;
  `tunnel_id` is null and the ingress blocker remains.

## Remaining gates

- Owner review and landing through protected repository governance.
- Add Contents: read-only to App `4916376` (browser action, separately
  authorized).
- Commit and owner-promote SMCBD's target manifest; record its exact
  commit/hash in the external service registry.
- Record authoritative OpenClaw and ClawSweeper reviewer actor identities.
- Provision isolated credentials, connector, HTTPS route and service state.
- Separate deployment and shadow-activation authorization; qualify genuine
  App-owned checks before any branch-protection rebinding.

Browser steps, selectors, prerequisites and rollback: `docs/smcbd-pilot-handoff.md`.

## Untouched boundaries

No App/settings mutation, key/secret creation, webhook, registry, tunnel,
service, deployment, target repository, branch protection, check rebinding,
merge, release, adjudication, x-api or live Blocks conductor change occurred.
PR #1/#2 history is unchanged.
