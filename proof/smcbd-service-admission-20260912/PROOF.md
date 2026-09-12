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

## Review repair after head `fe7e5f37` (Copilot, six findings)

The results recorded for `fe7e5f374569de02d98a6bf26ab0818e42d16c2b` (local
PASS; hosted run 34693364110 success) are historical for that head only. The
follow-up commit repairs every finding; none was dismissed:

1. **Registry inside checkouts (critical).** `read_service_registry` now takes
   forbidden roots; `serve` passes this source tree, the profile's target
   checkout, state root and proof root (which hold the reviewer inboxes). A
   valid 0600 registry inside any of them, including `nested/../` forms and a
   file in this repository, is refused.
2. **Registry never re-read (critical).** `registry_provider` re-reads and
   re-validates the file (and the profile's own enrollment) on every delivery
   and worker tick via `service_runtime.resolve_registry`. Promotion is
   observed by the next tick/delivery without restart; a registry that stops
   validating fails every later delivery and tick closed rather than reusing
   the previously loaded approval.
3. **ClawSweeper `workflow_run` binding (critical).** Bindings are created only
   by the accepted `pull_request` delivery that established the head, and only
   when its event head equals the current head. `workflow_run` deliveries
   (exact-head CI, ClawSweeper naming the current head, ClawSweeper naming an
   unrelated head, stale CI) and closed/duplicate pull-request deliveries never
   bind or read policy. Re-admission under a promoted policy happens through a
   new head or review epoch (`ready_for_review`, `reopened`).
4. **Mode exactly 0600 (moderate).** `stat.S_IMODE(...) == 0o600`; `0400` and
   `0700` are now rejected with the other modes.
5. **Worker before bind (moderate).** The handler and `BoundedHTTPServer` are
   constructed first; the worker starts only after a successful bind, inside the
   same cleanup path. A bind failure exits 2 with no worker thread and no tick.
6. **Policy not enforced against engine config (moderate).**
   `require_policy_matches_profile` re-parses the admitted manifest bytes and
   requires repository, default branch, CI workflow name/path, quiet period and
   merge policy to equal the core profile before binding; mismatch rolls the
   delivery back. Materializing the manifest as the engine profile remains
   listed as open work in `docs/migration.md`.

## Second review repair after head `5796910` (Copilot, four new findings)

The `5796910961caa3f124241dd36c3230fe4d2abcaa` results (local PASS; hosted run
34694603956 success) are historical for that head. A fifth comment in that
review was the original `fe7e5f3` policy-versus-profile thread re-anchored on
unchanged context; it is addressed by `require_policy_matches_profile` above.

7. **Unvalidated token permission map.** `GitHubAppClient.__init__` now refuses
   any `github_app.permissions` that is not exactly the legacy or standalone
   closed allowlist, so a directly constructed client cannot mint an
   over-privileged installation token whatever map it is handed.
8. **Registry TOCTOU.** `_registry_bytes` requires a same-user parent directory
   without group/world write bits, opens the file `O_RDONLY|O_NOFOLLOW|O_CLOEXEC`,
   runs the regular-file/owner/mode-0600/single-link/size checks on `fstat` of
   that descriptor, and reads bounded bytes from the same descriptor. A racing
   pathname swap after validation is proven to be ignored.
9. **Worker gate without profile/registry agreement.** `require_current_bindings`
   now performs the profile-to-registry lookup (`require_profile_enrolled`) on
   every tick, so a valid registry for another App/installation cannot pass the
   binding gate for a changed profile.
10. **Silent worker death.** Fail-closed `ContractError` ticks retry; any other
    worker exception is recorded, shuts the ingress server down and makes
    `serve` raise `ServiceStopped`, so the process exits 2 instead of accepting
    deliveries nothing will act on.

## Third review repair after head `8009e5e` (Copilot, two new findings)

The `8009e5e3e806098e132f4e81e7802d926f103fbb` results (local PASS; hosted run
34695283800 success) are historical for that head.

11. **Ancestor TOCTOU.** `_open_registry_descriptor` walks the canonical path
    from `/` with held directory descriptors (`O_RDONLY|O_DIRECTORY|O_NOFOLLOW|
    O_CLOEXEC`, `dir_fd=`), compares every held directory to the forbidden
    roots by `(st_dev, st_ino)`, validates the parent on its descriptor and
    opens the leaf relative to it. A parent swapped for a symlink after
    canonicalization is proven to be refused, not followed. The earlier
    pathname-based root comparison was removed so the identity check is the
    single guard.
12. **Reviewer actors from the profile.** The enrollment schema now carries a
    `reviewers {openclaw, clawsweeper}` block (non-empty, bounded, distinct);
    `require_profile_enrolled` also requires the engine profile's
    `review_policy.reviewers` to equal the enrollment, and runs on every
    provider read, worker tick and delivery. `preflight_enrollment` now derives
    the enrollment from the profile and compares the delivery against it, so
    the profile-to-registry lookup is a single guard.

## Fourth review repair after head `8366810` (Copilot, two new findings)

The `83668102fdc1df8337eaa32af14571cf87e1164c` results (local PASS; hosted run
34696196053 success) are historical for that head.

13. **Reviewer rotation left bindings valid.** Each binding row now records
    the enrollment's `reviewer_openclaw`/`reviewer_clawsweeper`;
    `binding_for_current_head` returns nothing unless the registry still names
    the same actors, so a consistent rotation in registry and profile blocks
    projection of every existing head until it is re-admitted under a new
    head/epoch.
14. **Profile edits after admission left bindings valid.** Each binding row now
    records `profile_digest`, the SHA-256 of the policy-governed engine-profile
    fields (repository, numeric ID, default branch, CI workflow name/path, quiet
    period, merge policy); the gate recomputes it from the currently loaded
    profile on every tick and refuses a mismatch, so a restart with an edited
    profile cannot unlock old bindings under different rules.

## Fifth review repair after head `cfe7908` (suppressed Copilot findings, six items)

The `cfe7908e34ade700208b53536558bf9b159526a3` results are historical for that
head. These items were listed as suppressed in the Copilot reviews at `8009e5e`
and `8366810` and were all reproduced against `cfe7908` before repair:

15. **Remote policy I/O under `BEGIN IMMEDIATE`.** `stage_approved_policy`
    fetches the bounded approved-policy bytes before the engine transaction,
    hash-verifies them against the enrollment and keeps at most eight immutable
    `(repository, commit, sha256)` entries; the admission hook's reader returns
    only the staged bytes and performs no I/O. The regression proves a second
    writer can take the write lock while the policy is read.
16. **Transient policy failures answered 400.** `GitHubTransientError`
    (transport errors, HTTP 429/5xx) is raised by the adapter, and the service
    turns it into `RetryableIngestError`, which the webhook maps to 503
    `dependency_unavailable` with nothing persisted so GitHub redelivers.
    Non-transient reader failures, malformed and foreign deliveries still
    answer 400.
17. **Leaf `is_symlink()`+`resolve()` TOCTOU.** Only the ancestors are
    canonicalized; the original leaf name is opened with `O_NOFOLLOW` under the
    held parent, so a leaf swapped for a symlink after canonicalization is
    refused, never followed.
18. **Symlink-loop `RuntimeError`.** `resolve()` failures of either kind are a
    fail-closed unavailable registry; `main` exits 2 without a traceback.
19. **`binascii.Error` in `read_policy`.** It is a `ValueError` subclass and was
    already normalized; the tuple now names it explicitly and a regression pins
    the behaviour for invalid padding/length.
20. **Cross-App `binding_id`.** Direct regressions in both suites prove two
    valid Apps with identical review tuple and policy get distinct binding IDs,
    and a mutant drops the App id from the canonical identity.

`tools/review_conductor_runtime.py` is an adapted extraction file; its
`docs/provenance.json` destination hash and adaptation note were updated for
the transient classification and 503 mapping.

## Verification at the repaired head (local CPython 3.14.6, macOS arm64)

- `make check` — PASS: legacy engine/activation/userland/profile suites,
  scaffold and repository guards, trusted admission (18), service runtime (35,
  including the mutation harness), launcher transport, workflow contract,
  extraction provenance (14 files) and Python compilation, `git diff --check`.
- `make build` — PASS; the packaged zipapp still excludes service, admission
  and runtime modules.
- `python3 -m compileall -q tools tests scripts` — PASS.
- `scripts/check_whitespace.py <base> <head>` — PASS (recorded in the PR).

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
10. accept any owner-only registry mode;
11. accept a registry inside a checkout or state root;
12. cache the registry instead of re-reading it;
13. bind on `workflow_run` deliveries;
14. bind without checking the policy against the engine profile;
15. start the worker before ingress binds;
16. mint tokens from an unvalidated permission map;
17. read the registry by pathname after validating the descriptor;
18. gate the worker without checking the profile enrollment;
19. let the worker die silently on operational failure;
20. follow a symlinked ancestor while walking the registry path;
21. trust the profile's reviewer actors instead of enrollment;
22. keep bindings current after reviewer rotation;
23. keep bindings current after the engine profile changes;
24. run policy transport inside the engine write transaction;
25. reject transient policy failures instead of asking for redelivery;
26. answer 400 for a retryable dependency failure;
27. classify transient GitHub statuses as rejected operations;
28. follow a symlinked registry leaf;
29. let a symlink loop escape as a traceback;
30. drop the App id from binding identity.

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
