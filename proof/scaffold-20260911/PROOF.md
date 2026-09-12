# Standalone Review Conductor scaffold — 2026-09-11

## Assignment and source

Owner: Smoky, one source-writing lane; requesting owner is closer.
Task: additive standalone scaffold, `mutate` only within the new repository.
Branch: `codex/standalone-scaffold`.
Bootstrap base: `c01a443` (README only; no prior default branch existed).
Worktree: `~/Developer/worktrees/review-conductor-scaffold`.
Source x-api: `48036abf1649a6fbd1738d68b23fc235893e0b68`, clean before extraction.
No x-api writes; no live enrollment, activation, mutation or merge authority.

The owner's follow-up established repo-owned review requirements. ADR 001
records the split between target manifests and trusted service enrollment.
Existing solution preflight selected the existing engine plus Python's standard
library; no replacement engine, new platform or runtime dependency was added.
Read source AGENTS/tools contracts, conductor contract, prior generalization
proof, repo/machine hygiene, and Swarm Delivery architecture boundaries.
Used the smoky-worktree skill; raw Git bootstrap was necessary for a new repo
with no remote base or existing native helper. All implementation is off main.

## Local checks

| Check | Result |
| --- | --- |
| `make check`: full extracted core integration suite | PASS |
| Shared adapter suite | PASS |
| Blocks userland regression suite | PASS, 18 tests |
| Isolated SMCBD profile suite | PASS, 9 tests |
| New manifest + isolated packaged-build suite | PASS, 4 tests |
| Extraction provenance and Python compilation | PASS, 14 source files verified |
| `make build` / packaged manifest CLI | PASS, valid and activation unsupported |
| Reproducible zipapp under isolated Python / temporary HOME | PASS; no service or state created |
| `actionlint .github/workflows/ci.yml` | PASS |
| `git diff --check` | PASS |

`candidate-manifest.json` binds tested code/config/workflow files by SHA-256.
Final commit SHA is recorded in the delivery and remote draft PR, avoiding a
self-referential commit hash. The build is only the offline manifest CLI; engine
and compatibility code are source/regression assets, not an activated release.
External OpenClaw/ClawSweeper review: NOT RUN / not enrolled. Hosted CI results
are reported separately after publication; local PASS does not stand in for them.

## Remaining work and stop condition

Scaffold complete when committed, draft publication attempted, and exact-head CI
observed or blockers reported. No merges, protection edits or service activation.
Trusted manifest admission, policy-hash binding, authenticated client API, adapter
qualification and a gated live migration remain in `docs/migration.md`.
SMCBD remains inactive. The examples were not committed to Blocks or SMCBD.
Gateway, Smoky, live conductor, target repositories/PR #3, credentials, staging,
production and adjudication state remain untouched.
