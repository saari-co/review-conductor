# PR #1 security/governance hardening — 2026-09-11

## Exact source and scope

- Repository: `saari-co/review-conductor`; PR #1 remains draft/open.
- Worktree: `/Users/cp-1/Developer/worktrees/review-conductor-scaffold`.
- Branch: `codex/standalone-scaffold`; one mutation owner for this slice.
- Starting head: `55c7d7b3ca4ff64595c83bdedd09515e9e7a139c`.
- Freshly fetched base: `c01a44382bfa66c0e3be39905dd2b9f47c6c7333`.
- Starting worktree was clean and matched the remote PR head. Existing PR branch
  was explicitly assigned; it was not replaced or rebased onto unrelated work.
- Used `smoky-worktree` for source isolation. Read repository/machine instructions,
  architecture, integration, migration and extraction ledger before edits.

This is a scoped foundation audit (governance, packaged input/build boundary,
legacy surface classification and regression preservation), not comprehensive
independent external security clearance or live-adapter qualification. Existing
GitHub CODEOWNERS/PR controls and standard-library checks were adequate; no new
service, runtime dependency or custom approval engine was introduced.

## Findings and dispositions

| Finding | Disposition |
| --- | --- |
| Missing two-owner governance / CODEOWNERS / PR security guidance | Added numeric owner map, catch-all CODEOWNERS, contributing/security guidance and PR template. Both owners independently verified through GitHub permission reads. |
| `.gitignore` alone cannot stop tracked or force-added material | Added bounded index + working-tree source guard, no symlinks/submodules, prohibited paths/types, size bounds and selected credential markers; negative tests cover staged-content hiding. This is not an exhaustive secret scanner. |
| CLI loaded whole manifest before applying byte limit | Read at most 16,385 bytes before validating maximum 16,384. Negative test asserts bounded read. |
| JSON accepted UTF-16/32 despite UTF-8 wire contract | Decode UTF-8 explicitly; reject alternative encodings. |
| Branch validation admitted hidden / intermediate `.lock` components | Reject those invalid Git ref components; update schema and positive/negative regressions. |
| Historical test included a synthetic private-key marker | Confirmed three-line bytes value with body exactly `fixture`; assemble from bytes to retain identical runtime value. No guard exception or real credential involved; provenance adaptation recorded. |
| Legacy source modules retain executable pilot surfaces / active legacy defaults | Explicitly classify as regression-only, prohibited for direct standalone deployment. Build allowlist still excludes all engine/launcher/profile files. No legacy engine or profile bytes changed. |
| Unprotected main, no base CODEOWNERS, no independent rails | Report as unresolved human bootstrap/enforcement blockers; provide an inert exact protection proposal, not a setting change or approval. |
| Trusted enrollment/policy-hash/API/outbox/adapters absent | Preserve explicit inactive stop line and ordered implementation/cutover roadmap. No runtime-readiness claim. |

Owner mapping observed read-only: `saariuslystoned` -> GitHub user `159389674`,
`saarius` -> `83989817`; both admin/write. This governance mapping is not trusted
service reviewer enrollment. CODEOWNERS means either owner; extra both-owner
sensitive-change acknowledgement is a human rule, not claimed platform enforcement.

## Applicable local checks

All passed for the candidate code recorded in `candidate-manifest.json`:

- `make check`: repository source/owner guard; complete extracted core integration
  and shared-adapter suites; 18 userland tests; nine isolation/profile tests;
  six scaffold tests; five hygiene/ownership negative tests; 14-file extraction
  provenance and Python compilation; whitespace check.
- `make build`: deterministic independent zipapp. Packaged regression verifies
  isolated Python/temp HOME, non-activation, reproducibility and exact allowlist.
- `actionlint .github/workflows/ci.yml`: PASS.
- `git diff --cached --check`: PASS.
- Governance/protection/schema JSON parse: PASS.

Only the two offline CLI modules and `__main__.py` are packaged. The core exact
repository/PR/base/head/epoch, HMAC/replay, stale-evidence, draft/quiet-period,
isolation, bounded-repair and human-only merge behavior remains regression tested.

The final commit identity, post-commit local results, hosted workflow/check URLs,
issuer and head/base stability are reported after push, outside this immutable
proof to avoid a self-referential commit. Hosted PASS must be observed at that
exact resulting head, not inferred from starting-head CI or local tests.
External OpenClaw / ClawSweeper reviews: **NOT RUN / not enrolled**, not PASS.
No independent human review/approval or bootstrap adjudication is claimed.

## Protection inspection and proposal (no mutation)

Read-only GitHub observations: `main.protected=false`; classic protection HTTP
404 `Branch not protected`; repo/inherited rulesets `[]`; effective main rules
`[]`; auto-merge false. Base has no CODEOWNERS; CODEOWNERS errors on the absent
base file returned 404, not a successful validation. Draft-head validation is
performed separately after publication.

Observed CI issuer is GitHub Actions, App `15368`. Proposed complete API body is
`docs/main-protection.proposed.json`; rationale/limits in `docs/branch-protection.md`.
Require strict `CI` from that App, one non-author CODEOWNER approval, dismiss stale
approvals, latest-push approval and resolved threads; enforce admins; restrict
pushes and dismissal to both owners; no bypass users/teams/apps, force pushes or
deletions. Only aggregate `CI` is proposed now, no unobserved external check names.
No protection endpoint was written. Apply/readback/mutation verification requires
later owner authorization and a fresh read to preserve intervening settings.

## Remaining blockers and boundaries

- Explicit human bootstrap decision and exact-head owner review are pending.
- Main remains unprotected; CODEOWNERS in the PR cannot protect its own base.
- No independent external review, trusted registry/policy binding, authenticated
  service API/outbox, qualified live adapters, credentials, ingress or deployment.
- Scaffold remains undeployed/inactive. No target enrollment, PR readiness, merge,
  adjudication, approval record, branch-protection/settings or live changes.
- Gateway, Smoky, x-api, live conductor, Blocks, SMCBD/PR #3, staging and production
  are outside the mutation scope. No notifications or source-conversation sends.

## Changed files (excluding this proof and its candidate hash manifest)

- `.github/CODEOWNERS`
- `.github/owners.json`
- `.github/pull_request_template.md`
- `.gitignore`
- `AGENTS.md`
- `CONTRIBUTING.md`
- `Makefile`
- `README.md`
- `SECURITY.md`
- `contracts/target-manifest.schema.json`
- `docs/bootstrap.md`
- `docs/branch-protection.md`
- `docs/main-protection.proposed.json`
- `docs/migration.md`
- `docs/provenance.json`
- `scripts/check_repository.py`
- `tests/test_repository_guard.py`
- `tests/test_review_conductor_userland.py`
- `tests/test_scaffold.py`
- `tools/conductor_cli.py`
- `tools/target_manifest.py`
