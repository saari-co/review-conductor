# Trusted enrollment registry and approved policy binding — 2026-09-11

## Exact source and scope

- Repository: `saari-co/review-conductor`; PR #1 remains draft; nothing pushed.
- Managed worktree branch: `openclaw/review-conductor-trusted-admission-fable`,
  one mutation owner for this slice.
- Verified starting head before any edit: `dadcf07c8c714e2437c7957bdafd1b93b04347ef`
  (PR #1 `codex/standalone-scaffold`), clean tree, matching the expected head.
- Base: `c01a44382bfa66c0e3be39905dd2b9f47c6c7333` (main).
- Read AGENTS.md, CONTRIBUTING.md, SECURITY.md, architecture, integration,
  migration and bootstrap contracts before editing.

Slice: service-owned enrollment registry contract, approved base-policy
loading with content-hash verification, and deterministic binding of the
admitted policy identity to the exact repository/PR/base/head/epoch tuple.
Source-only, standard library only, inactive by default.

## Implementation

`tools/trusted_admission.py` (new, library only):

- `INITIAL_ENROLLMENT_SCOPE` pins exactly `dinkuskit/blocks` → `1306882611` and
  `saari-co/openclaw-smcbd-suite` → `1366416798` (numeric IDs already tracked in
  the historical profiles). Any other name, or any other numeric ID for those
  names, is rejected at registry load. Expanding scope is a source change under
  owner review, not a registry edit.
- `load_registry` accepts a strict UTF-8 JSON document (≤ 65536 bytes, no
  duplicate/unknown keys) with per-enrollment `repository`, `repository_id`,
  `installation {id, account}` and `approved_policy {commit, sha256}`. The
  installation account must be the repository owner; names, repository IDs and
  installation IDs must be unique. No registry document is committed.
- `Registry.lookup` requires repository name, numeric ID and installation ID to
  agree with one enrollment; type confusion (strings, booleans) fails.
- `load_approved_policy` refuses any commit other than the approved one before
  the injected `read_policy` transport is called, then requires exact-byte
  SHA-256 equality, v1 manifest validity and repository-name agreement.
- `admit` validates an exact-key request, builds a frozen `ReviewTuple`
  (owner/name, positive IDs, lowercase 40-hex SHAs, base ≠ head, epoch ≥ 0),
  refuses PR-head self-promotion, and returns a frozen `Admission` whose
  `binding_id` hashes canonical JSON over the full tuple, installation and
  `policy_id`. `policy_is_current` reports stale bindings after promotion,
  hash change or de-enrollment.
- Follow-up (review finding on the first commit): every dataclass now enforces
  its own invariants in `__post_init__` (`Enrollment`/`AdmittedPolicy` pinned to
  the enrollment scope and numeric IDs, `Registry` uniqueness, `Admission`
  positive-int installation and review-tuple/policy repository+ID coherence).
  `Admission.revalidate()` rebuilds all components; `binding_id` calls it and
  `policy_is_current` returns false for malformed, incoherent or crafted objects
  and additionally compares enrollment repository/ID/installation, not only
  commit/hash.

Not packaged: `scripts/build.py` allowlist and the packaged-namelist regression
are unchanged; the CLI still exposes only `validate-manifest`. No imports of
os/sqlite3/subprocess/urllib/socket; a regression asserts that.

## Tests (`tests/test_trusted_admission.py`, 16 tests)

- Registry scope exactly two repositories; forks, case variants, this repo and
  x-api rejected.
- Mismatched numeric IDs (including the other target's ID), string/bool IDs,
  cross-account installation, zero/null installation, uppercase/non-hex commit,
  short hash, extra credential-like keys, missing sections.
- Duplicate repository / shared installation, wrong schema, extra keys, non-UTF-8,
  UTF-16, deep nesting, oversized, non-bytes input.
- Lookup with every single component swapped to the neighbouring enrollment.
- Approved-commit loading: only the approved commit reaches transport; head,
  base, other repo's commit, uppercase and empty commits never call the reader.
- Manifest mutation at the approved commit: whitespace, re-serialisation, other
  repository's manifest, empty, oversized, non-bytes all fail the hash check;
  hash-matching but invalid or misnamed manifests fail the validator/name check.
- Deterministic identity: same inputs → same `policy_id`/`binding_id` across
  fresh fixtures; commit, bytes, name, numeric ID, base, head, epoch and PR
  number each change the identity.
- Cross-org/cross-repo isolation: Blocks tuple with SMCBD installation, SMCBD
  name with Blocks ID, SMCBD approved commit for Blocks, etc. all fail closed.
- PR-head manifest mutation cannot self-promote (transport never read for head).
- Stale policy: promotion invalidates the in-flight binding; re-admission at the
  new commit yields a new binding; old registry does not vouch for the new one.
- Library inert in CLI/build sources.
- Direct construction: `Admission` with an SMCBD policy on a Blocks tuple, bad
  installation types, non-dataclass components; `dataclasses.replace` on policy
  and enrollment with foreign names/IDs/accounts/invalid fields; duplicate or
  non-`Enrollment` registry entries; and `object.__setattr__`-crafted admissions
  (foreign policy, string installation, foreign tuple repository, base == head,
  foreign policy ID, `None` tuple) are never current and raise on `binding_id`
  and `revalidate()`.

Mutation spot-check during development: disabling the numeric-ID pin, hash
check, commit check, installation check, epoch in the binding or manifest-name
check each produced at least one failing test. For the follow-up, removing the
tuple/policy coherence check, the `revalidate()` call in `policy_is_current` or
`binding_id`, the admission installation check, or the policy numeric-ID pin
each produced failing tests.

## Applicable local checks (all passed, candidate hashes in `candidate-manifest.json`)

- `make check`: repository guard; extracted integration, shared-adapter, 18
  userland and 9 profile regressions unchanged and passing; 6 scaffold tests;
  16 trusted-admission tests; 5 guard tests; 14-file provenance and compile;
  `git diff --check`.
- `make build`: deterministic zipapp; packaged reproducibility/allowlist test
  passes with the new module excluded.
- Local interpreter was CPython 3.14 only; 3.11/3.12 were not installed here.
  The module uses no post-3.10 syntax; hosted CI on 3.11/3.12 is not yet observed.

External OpenClaw / ClawSweeper reviews: **NOT RUN / not enrolled**, not PASS.
No hosted CI observed for this head (not pushed). No human review claimed.

## Boundaries preserved

- No GitHub App, installation, credential, webhook, ingress, service, deployment,
  branch protection, merge, adjudication or readiness change.
- No change to x-api, `dinkuskit/blocks`, `openclaw-smcbd-suite`, PR #3, Gateway,
  Smoky, legacy engine modules, profiles or the extraction ledger.
- No push, no PR created or modified.

## Changed files

- `Makefile`, `README.md`, `SECURITY.md`
- `docs/bootstrap.md`, `docs/integration-contract.md`, `docs/migration.md`
- `docs/trusted-admission.md` (new)
- `tests/test_trusted_admission.py` (new)
- `tools/trusted_admission.py` (new)
- `proof/trusted-admission-20260911/` (this proof and candidate hashes)
