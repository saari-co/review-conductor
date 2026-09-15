# Authenticated cold hydration — source qualification

## Route and source

- Repository: `saari-co/review-conductor`
- Owner: `conductor_cold_fetch_repair`; final coordinator: root.
- Branch: `codex/authenticated-cold-hydration-20260915`.
- Fresh remote base: `261f886847f8badfcf1d5affdbdd7a1f4c1e092a`.
- Mode: mutate source in one isolated worktree; no live service changes.
- Purpose: missing private-repository objects must use the admitted service App,
  not ambient operator Git authentication or warm shared objects.
- Stop: tested committed draft PR and exact-head CI, then root-owned review,
  merge and separately coordinated live activation/recovery.

## Implemented boundary

GitHub [documents installation-token HTTP Git access](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/authenticating-as-a-github-app-installation).
The existing service App client supplies its already-scoped installation token
only to a fixed helper through a one-use anonymous pipe. There is no operator
credential lookup, new selector, permission grant or auth-mode fallback.

An independent temporary bare object store isolates authenticated Git from target
configuration, URL rewrites, credential helpers and hooks. Global/system config,
redirects, prompting, parent trace variables, ambient proxy and operator HOME
credentials are excluded. The helper binds HTTPS/github.com/exact repository and
refuses repeat credential lookup after its pipe is consumed. No token enters
argv, environment, a file, logging or proof. Only descriptor identifiers enter
argv and Git's credential helper configuration. Service generation inheritance
is preserved alongside the owned credential pipe.

The fetch requests the PR ref and explicit base, verifies current ref equals the
admitted head and base ancestry, creates a pack bounded to 256 MiB before import,
and imports through `index-pack --strict`. Checkout HEAD, refs, origin and clean
worktree are preserved. Authority is rechecked after credential resolution and
before/after import; wrong tuples and revoked admission do not enqueue reviews.
A failed action retains a closed reason class instead of raw Git output.

## Verification

- Eleven cold-hydration regressions PASS: independent object stores, real Git
  fetch/pack/import, actual Git helper exchange using synthetic credential bytes,
  unchanged refs/HEAD/worktree, missing auth, wrong ref, wrong repo, missing
  admission, revoked authority after token resolution and before import,
  host/path/protocol refusal, one-use behavior, failure cleanup, generation
  descriptor preservation, no credential values in argv/environment, and no
  parent trace/resolver environment inheritance.
- Existing userland tests: 21 PASS. Existing service-runtime tests: 72 PASS.
- `make build`: PASS.
- `make check`: PASS, exit 0 (including complete existing supervisor mutation and Blocks coverage).
- Primary cold-store regression fails against the unchanged baseline function
  before any transport: it attempts network hydration in the target checkout.
  The same test passes the repaired isolated-store path.

## Limitations and activation gates

This is offline source proof, using synthetic credentials and a test-only local
transport substitution. It does not prove GitHub private-object HTTP access,
credential availability, deployment, registry health or the live PR review. Root
must verify cold private-object hydration before the supported authority-bound
retry; the original enqueue never reached a reviewer. No reviewer rerun,
maintenance retry, live database edit, service restart, credential/account change
or protection change was performed by this lane.

The production fetch is read-only upstream but writes validated Git objects
locally. Temporary staging is self-cleaning on ordinary completion/failure;
process death may leave a private temporary directory containing repository
objects (never the token). Timeout is bounded per command, not whole repository
size; the pack-size bound is checked before importing it into the checkout.

## Tested source hashes

| File | SHA-256 |
| --- | --- |
| `tools/review_conductor_runtime.py` | `09d97f3c2524631a6285fef5d99de11c1f7e8e099287293f2af0d9abf31f0328` |
| `tools/review_conductor_userland.py` | `5fd67bfb657aca9773afb39a108ff05e833352da4f31e69dd2f024bd10f3a49d` |
| `tools/git_hydration_credential.py` | `5eb0d630e7f15cdb14e65eab3d1a2aa0dd7a0de7df5cc4e7c224a1092c1bf44d` |
| `tests/test_cold_hydration.py` | `dbdb880beb1b5dc309ebf4c909a59084aa713c7487ea55c042498c026ef4306d` |
| `tests/test_review_conductor_userland.py` | `330fdd909156db433023534bee115b1972778d8c2e63906026bf26c1aae85cb1` |
| `Makefile` | `f4605ccf13e04577209516ae3183446b126f661f566cf90a25c1f12d559b7341` |
