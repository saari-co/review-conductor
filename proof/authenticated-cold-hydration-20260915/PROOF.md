# Authenticated cold hydration — source qualification

## Route and source

- Repository: `saari-co/review-conductor`
- Owner: `conductor13_round1_repairs`; final coordinator: root.
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
must verify live cold private-object hydration during the separately coordinated
deploy → supported authority-bound retry → normal service fetch; no supported
pre-retry qualification operation exists. The original enqueue never reached a reviewer. No reviewer rerun,
maintenance retry, live database edit, service restart, credential/account change
or protection change was performed by this lane.

The production fetch is read-only upstream but writes validated Git objects
locally. Temporary staging is self-cleaning on ordinary completion/failure;
process death may leave a private temporary directory containing repository
objects (never the token). Timeout is bounded per command. The round-one resource limits below apply;
they are not a whole-host disk or memory quota.

## Copilot round-one adjudication

Review `5215434527` on `31e18dcbf7806d0f0fb2a57fe86b73b6245c2313`:
all seven observations `required_fix → repaired` (the import-fence observation
is duplicated between inline and suppressed findings).

- Immediate authority fences execute inside the generation-bound runner after
  credentials yield / pack opening and directly before authenticated fetch or
  checkout import. Revocation remains `AuthorityDenied`, not action failure.
- Pipe creation, atomic write (including short write), descriptor setup and local
  temporary-directory/pack I/O fail with sanitized contract reasons. Owned FDs
  close, and cleanup does not replace an admission-denial exception.
- Legacy unavailable capability is distinct from revoked standalone authority.
  Pending-action coverage proves persisted `failed`/one attempt/closed reason,
  no second-tick retry, and retained pending state on actual revocation.
- Fixed Git exec wrapper applies a 256 MiB per-file limit during packed fetch,
  repack and import; 180-second per-process CPU limit; Linux 2 GiB DATA/AS limits.
  Post-fetch stored bytes are capped at 256 MiB. Expanded inventory is capped at
  512 MiB and 100,000 objects before checkout import. The inventory is streamed
  from a bounded file; repository object bodies never enter Python memory.
- Eight new regression methods bring cold-hydration coverage to 19 tests. Seven
  primary authority/resource/state/budget regressions fail on the original
  `31e18dcb` source and pass repaired source. Synthetic independent Git stores
  cover an incompressible fetched pack exceeding a lowered child file cap and a
  highly compressible object exceeding the expanded budget without checkout import.

### Resource boundary limitations

These are not total-host or aggregate-network quotas. Incoming fetch is forced
to remain packed; each file is capped while written, then total stored objects
are checked before import. Staging index-pack may decompress data before the
expanded inventory is available. Linux has additional per-process memory limits;
macOS rejected DATA limit setup in qualification and does not reliably enforce
AS, so no hard macOS memory bound is claimed. One-thread Git and bounded pack
windows reduce pressure but are not such a guarantee. Process-group lifecycle
and existing wall timeouts remain unchanged. Service shutdown, credential access
and live GitHub fetch are not exercised by these synthetic tests.

References: [Git fetch unpack limit](https://git-scm.com/docs/git-config),
[Python resource limits](https://docs.python.org/3/library/resource.html).

## Tested source hashes

| File | SHA-256 |
| --- | --- |
| `tools/review_conductor_runtime.py` | `e591aaf37b9f69083a58ba120629d50ce98c6236bc2260a509b1f2a6373aa4e5` |
| `tools/review_conductor_userland.py` | `a8c463d909481a0d7cd13d51060c625bae64fd7d70210375b5ebfeeda6f4bfd4` |
| `tools/git_hydration_credential.py` | `5eb0d630e7f15cdb14e65eab3d1a2aa0dd7a0de7df5cc4e7c224a1092c1bf44d` |
| `tools/git_hydration_exec.py` | `170b25caefd1a15a7d2315d8449180e6797d203f264cdc9e42bf3fd2d9a6e511` |
| `tests/test_cold_hydration.py` | `be464356f189f166e406003fa2b526ac2d9741b3f0f5ec41dd211cc87be0e414` |
| `tests/test_review_conductor_userland.py` | `330fdd909156db433023534bee115b1972778d8c2e63906026bf26c1aae85cb1` |
| `Makefile` | `f4605ccf13e04577209516ae3183446b126f661f566cf90a25c1f12d559b7341` |
