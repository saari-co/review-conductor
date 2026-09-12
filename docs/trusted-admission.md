# Trusted admission v2: enrollment registry and approved policy binding

`tools/trusted_admission.py` is a standard-library-only module that the future
service calls before any review work. It is not packaged in the zipapp, has no
CLI command, performs no I/O, and reads no credentials. All failures raise
`AdmissionError` before any admission value exists; messages name the check and
never echo untrusted data.

## Enrollment registry (`review-conductor.enrollment.v2`)

Service-owned JSON, at most 65536 bytes, strict UTF-8, no duplicate keys, no
unknown keys. It is never read from a reviewed repository and never committed
to this repository; tests build synthetic registries in memory.

```json
{
  "schema": "review-conductor.enrollment.v2",
  "enrollments": [
    {
      "repository": "dinkuskit/blocks",
      "repository_id": 1306882611,
      "github_app": {
        "id": 0,
        "installation_id": 0,
        "installation_account": "dinkuskit"
      },
      "approved_policy": {"commit": "<40 lowercase hex>", "sha256": "<64 lowercase hex>"},
      "reviewers": {"openclaw": "<openclaw-actor>", "clawsweeper": "<clawsweeper-actor>"}
    }
  ]
}
```

Rules enforced by `load_registry`:

- `repository` must be one of exactly `dinkuskit/blocks` and
  `saari-co/openclaw-smcbd-suite` (`INITIAL_ENROLLMENT_SCOPE`). Expanding that
  map is a separate explicit owner enrollment decision, made in source review,
  not by editing a registry document.
- `repository_id` must equal the numeric identity recorded for that name
  (`1306882611`, `1366416798`, from the historical profiles). Any other value
  for those names is rejected, so a registry cannot rebind a name to another
  repository.
- `github_app.id` and `github_app.installation_id` are positive integers and
  `github_app.installation_account` must be the
  repository owner segment. A Saari installation cannot serve a Dinkus repository
  or vice versa. The inactive SMCBD profile records the non-secret App and
  installation IDs; the illustrative `0` above is invalid and would be rejected.
- `approved_policy` is the approved default-branch commit plus the SHA-256 of the
  exact manifest bytes at that commit. Both are required.
- Repository names, numeric IDs and installation IDs must be unique across the
  registry.
- `reviewers.openclaw` and `reviewers.clawsweeper` are the authoritative
  reviewer actor identities (non-empty, bounded, distinct). The service refuses
  to serve, tick or accept deliveries while the engine profile's
  `review_policy.reviewers` differs from the enrollment, so a profile cannot
  supply or change the actors the engine trusts.

`Registry.lookup(repository, repository_id, app_id, installation_id)` succeeds
only when all four agree with one enrollment; strings, booleans or a neighbouring
enrollment's values fail.

## Approved base-policy loading

`load_approved_policy(enrollment, commit, read_policy)`:

1. Refuses any `commit` other than `approved_policy.commit` before transport is
   consulted. A PR head, base SHA, newer default-branch commit or another
   repository's approved commit never reaches the reader.
2. Calls the service-supplied `read_policy(repository, commit)` and requires raw
   bytes of at most 16384 bytes.
3. Requires `sha256(bytes) == approved_policy.sha256`. Whitespace changes,
   re-serialisation, another repository's manifest, empty or oversized content
   all fail. The hash is over exact bytes, not semantic JSON.
4. Validates the bytes with the v1 manifest validator and requires
   `manifest.repository == enrollment.repository`.

The result is a frozen `AdmittedPolicy` that retains the exact immutable manifest
bytes. Construction and revalidation recompute their digest, parse them again and
require the exposed `quiet_seconds` and `default_branch` values to match the
validated content. Shape-valid replacement or crafted-object mutation therefore
cannot preserve a current binding. `policy_id` is the SHA-256 of a
canonical JSON of `(schema, repository, repository_id, commit, sha256)`. Same
inputs always produce the same identity; any component change produces a
different one.

## Tuple/epoch binding

`admit(registry, request, read_policy)` takes an untrusted request with exactly
`repository, repository_id, app_id, installation_id, pr_number, base_sha, head_sha,
review_epoch, policy_commit`. It resolves the enrollment, constructs a validated
frozen `ReviewTuple` (owner/name, positive IDs, 40-hex lowercase SHAs, base ≠
head, epoch ≥ 0), refuses `policy_commit == head_sha` unless that is already the
approved commit, and loads the approved policy. The frozen `Admission` exposes
`binding_id`: the SHA-256 of canonical JSON over repository, repository_id,
pr_number, base_sha, head_sha, review_epoch, App ID, installation ID and `policy_id`.
Any base/head/epoch/PR change, and any policy promotion, yields a new binding.

`policy_is_current(admission, registry)` is true only while the registry still
approves exactly the bound commit and hash for that repository and installation.
Policy promotion, de-enrollment or a changed hash makes in-flight evidence stale;
the service must re-admit at the current tuple rather than carry evidence forward.

## Object coherence

Every dataclass validates its own invariants in `__post_init__`: `Enrollment`
and `AdmittedPolicy` are pinned to `INITIAL_ENROLLMENT_SCOPE` names and numeric
IDs, and `AdmittedPolicy` re-derives its exposed policy fields from the exact
hash-bound manifest bytes; `Registry` enforces uniqueness; `Admission` requires a positive-int
installation and that the review tuple and policy identify the same repository
and numeric ID. `Admission.revalidate()` rebuilds every component from its
fields, so an object crafted around `__post_init__` (for example with
`object.__setattr__`) fails. `binding_id` calls it before hashing and
`policy_is_current` returns false for any malformed or incoherent object. A
well-formed object naming another enrollment's installation is only detectable
against the registry, where it is never current and cannot be re-admitted.

## What this does not do

The inert library itself performs no I/O. `tools/service_runtime.py` composes it
with authenticated webhook ingress, GitHub approved-policy transport and atomic
SQLite binding persistence. Only the accepted `pull_request` delivery that
established a head binds it; `workflow_run` deliveries and closed/duplicate
pull-request deliveries never create bindings. One exact tuple keeps exactly one
binding, so a promoted policy applies only to a newly admitted head or review
epoch (for example `ready_for_review` or `reopened`), and the binding table
refuses a conflicting rebinding outright. Before binding, the admitted manifest
must agree with the engine profile's repository, default branch, CI workflow,
quiet period and merge policy. Each binding records the App, installation,
policy commit/hash, the enrollment's reviewer actors and a digest of those
policy-governed profile fields plus the adapter authority fields (ClawSweeper
workflow id/name/path/ref/publish, the adapter contract/artifact prefix and the
OpenClaw operator id/transport/worktree shelf and effective Spark dispatch
target);
a binding is current only while the registry
still approves the same policy and names the same reviewers and the engine
profile still digests identically, so reviewer rotation or a profile edit after
admission blocks projection until the tuple is re-admitted.
Databases created by the inactive service-core PR predate `profile_digest`.
The next accepted binding candidate adds that nullable column in place;
merely restarting does not migrate it. Pre-migration rows remain `NULL` and
therefore cannot satisfy the read-only authority gate until the exact tuple is
admitted under the current profile.

Approved-policy transport never runs inside the engine's SQLite write
transaction and only runs for deliveries that need a binding. The admission
hook, once the engine has classified a delivery as a new non-closed binding
candidate, reads the policy bytes from an in-memory stage; if they are not
staged it unwinds the transaction (nothing written), the service fetches the
bounded bytes outside any lock, hash-verifies them against the enrollment,
keeps at most eight immutable `(repository, commit, sha256)` entries under a
lock that is never held during a fetch, and makes at most three engine attempts
(at most two completed staging cycles) before returning a retryable failure if
registry or cache changes keep racing admission. Closed,
duplicate and stale deliveries therefore never touch the policy dependency. A
transient transport failure (connection error, timeout, HTTP 429/5xx) is a
retryable service failure: the webhook answers 503 `dependency_unavailable` and
nothing is written, so the delivery can be redelivered from GitHub's delivery
log or API (GitHub does not retry automatically). Malformed, foreign or
otherwise rejected deliveries still answer 400. The hook re-reads the registry
inside the delivery transaction, immediately before admission, and resolves the
enrollment against the core profile the engine reloaded for that delivery; a
revocation after preflight rejects the delivery with nothing written, and a
promotion is staged for the promoted enrollment before the delivery is re-run.
A profile whose `review_policy.enabled` is no longer true fails every provider
read, delivery and tick closed. Every external side effect of a worker tick —
each mutating GitHub call (fenced after token minting, immediately before the
request), checkout hydration, each OpenClaw command and review-result
notification delivery — re-runs the adapter's authority guard against the exact
repository/PR/base/head/review-epoch tuple that owns the side effect, using a
fresh read-only view of the engine database. A newer valid binding cannot clear
superseded work selected for an older tuple. Superseded-check cleanup and
unbound operator alerts remain repository-level maintenance and still require
all live tuples to be bound. A denial
before transport is a distinct recoverable outcome: the claim is released and
left pending, rather than being terminalized as a failed or uncertain external
side effect.

That source is inactive until an external
service registry (mode exactly 0600, single link, non-writable same-user parent,
outside source, checkout, state and proof roots, opened without following
symlinks and read from the validated descriptor, re-read on every delivery and
tick, and always enrolling the running profile) with at least one enrollment,
credentials, an enabled profile and an HTTPS edge exist. No event
outbox, deployment, live check publication, adjudication or merge behaviour is
activated; the packaged scaffold still exposes only `validate-manifest`.
