# Trusted-admission Copilot review repair — 2026-09-12

## Exact source and authority

- Repository: `saari-co/review-conductor`
- Pull request: #2, retained as draft
- Branch: `codex/trusted-admission`
- Starting and remote head: `15f6e5ca1b882e4b198b050e680c6037439f8b1a`
- Base: `0d1d7972c9814e113e924fc536b8839f33af9319`
- Scope: repair Copilot review comments `3994865958` and `3994865966`
- Authorized mutations: source, tests, documentation, proof, commit and push to
  the existing PR branch; no review-thread resolution, merge, deployment,
  credentials, App settings, enrollment activation or target-repository change.

## Repairs

1. `AdmittedPolicy` now retains the exact immutable manifest bytes. Construction
   and `Admission.revalidate()` recompute their SHA-256, parse them with the v1
   validator and require the exposed `quiet_seconds` and `default_branch` values
   to agree with those bytes. Shape-valid field replacement and crafted-object
   mutation can no longer retain a current binding.
2. Exceptions raised by the injected policy reader are normalized to the public
   `AdmissionError("approved policy content is unavailable")` contract. The
   original exception is retained as `__cause__`; no untrusted exception text is
   copied into the public message.

## Negative regression evidence

- Before the repair, changing `quiet_seconds` to 601 or `default_branch` to
  `forged` retained the same policy and binding identities and
  `policy_is_current()` returned true.
- Before the repair, injected `TimeoutError`, `OSError` and `RuntimeError`
  exceptions escaped directly.
- A disposable-copy mutant removing both manifest-derived field comparisons was
  rejected by
  `BindingTests.test_directly_constructed_incoherent_objects_fail_closed`.
- A disposable-copy mutant removing reader exception normalization was rejected
  by
  `PolicyLoadingTests.test_policy_reader_failures_use_the_public_admission_error_contract`.
- Each mutant first asserted that the intended source substring was actually
  removed; neither mutation touched the candidate worktree.

## Local verification

Captured at `2026-09-12T03:03:00Z` with CPython 3.14.6:

- `python3 -m unittest tests/test_trusted_admission.py` — 17/17 PASS
- `make check` — PASS, including all legacy conductor, profile, launcher,
  workflow, repository-guard, scaffold, provenance and compilation regressions
- `make build` — PASS; deterministic packaged allowlist remains unchanged
- `python3 -m compileall -q tools tests scripts` — PASS
- Working/index `git diff --check` — PASS

Hosted CI and fresh Copilot review were not yet run when this local proof was
written. They are required on the resulting exact head before review clearance.

## Boundaries and limitations

- The library remains offline, standard-library-only and excluded from the
  packaged CLI.
- No live registry, GitHub App credential, webhook, database, service, adapter,
  check publication, deployment or migration was exercised.
- CI success and this synthetic proof are not OpenClaw or ClawSweeper review PASS.
- PR review threads remain reviewer-owned evidence and are not resolved by this
  repair.
