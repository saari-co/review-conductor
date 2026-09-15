# Git credential array metadata — bounded repair

## Assignment and source

- Repository: `saari-co/review-conductor`.
- Sole source owner: `/root/deploy13_recover_suite4`; final coordinator: root.
- Branch: `codex/git-credential-capabilities-20260915`.
- Fresh remote `main` base: `dc80988267262875d78f620b5ec3def64589388b`.
- Isolated sibling worktree created with `bin/smoky worktree new ... --from <base>`.
- Mode: source mutation only. Stop: committed draft PR with passing exact-head CI.
- Root retains review, merge, deployment and authority-bound runtime retry.

## Confirmed defect and correction

The actual installed Apple Git 2.50.1 remote HTTP transport supplies repeated
`capability[]` negotiation entries (`authtype` and `state`). Multiple
`WWW-Authenticate` response headers likewise become repeated `wwwauth[]` keys.
The previous helper treated every repeated key as malformed and returned before
consuming its anonymous credential pipe. The existing `git credential fill`
test did not exercise real remote HTTP negotiation metadata.

The helper now ignores only the known `capability[]` and `wwwauth[]` metadata
arrays within its existing total input bound. It neither advertises support for
those capabilities nor uses challenge contents to select credentials. Duplicate
singleton keys remain rejected. Exact HTTPS, `github.com`, repository path,
regular FIFO type, one-use bounded pipe read, and no-store behavior are unchanged.
No auth scope, credentials, host allowlist, retry policy or runtime state changes.

## Verification

- Real `/usr/bin/git` remote HTTP challenge test: PASS. A loopback-only synthetic
  server first challenges, then verifies the synthetic credential and advertises
  a ref; `git ls-remote` exits zero. Repeated challenge metadata passes through
  the fixed production helper, and the anonymous pipe is consumed once.
- Negative singleton protocol/host/path duplicate and wrong-repository cases:
  PASS; rejection occurs without consuming the pipe, then the valid exchange
  succeeds using that same still-unconsumed descriptor.
- Excessive repeated metadata: rejected under the existing input bound without
  credential consumption.
- Both primary tests fail against the unchanged base helper and pass this fix.
- Entire cold-hydration test file: **24 tests PASS** (21 existing plus 3 new).
- `make build`: PASS. Full `make check`: PASS, exit 0 (including supervisor
  mutation tests and existing Blocks regressions). Exact-head CI is recorded in
  the PR body and parent closeout receipt after publication.
- No new provenance-ledger entry: these two source/test files are not extracted
  files listed in `docs/provenance.json`; existing ledger remains unchanged.

## Test limits and live gate

The HTTP fixture uses synthetic values only. A test-only helper adapter changes
only the loopback HTTP protocol/host fields to the production helper's fixed
HTTPS/GitHub test identity; it preserves actual Git-generated array metadata and
the inherited pipe. This is protocol and process-chain proof, **not TLS or live
private-GitHub authentication proof**. No real credential or auth logs were read;
no live fetch, deployment, retry, production checkout write or PR4 edit occurred.
Root must separately deploy qualified source and verify the next supported
normal-service cold fetch; this result does not certify that no other live
transport problem remains. No extra review was requested by this source lane.

## Tested source hashes

| File | SHA-256 |
| --- | --- |
| `tools/git_hydration_credential.py` | `3179ae2fb4de6c8941f49e55a919412b1da0983dc5b4f93e1ee32d00664455db` |
| `tests/test_cold_hydration.py` | `401f3b1f01925ba4b8082482b09c4ec9df805c7946fbf0fa84c70f2ad03c9120` |
