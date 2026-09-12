# PR #1 validated review repairs — 2026-09-11

## Source and authority

- Repository: `saari-co/review-conductor`, PR #1.
- Assigned existing worktree: `/Users/cp-1/Developer/worktrees/review-conductor-scaffold`.
- Branch: `codex/standalone-scaffold`; follow-up commit only, no amend/rebase.
- Starting local and remote head: `dadcf07c8c714e2437c7957bdafd1b93b04347ef`.
- Exact PR base: `c01a44382bfa66c0e3be39905dd2b9f47c6c7333`.
- Clean matching local/remote preflight passed. Read AGENTS, architecture,
  integration, contributing, security, bootstrap, migration and provenance docs.
- PR was unexpectedly ready at preflight; converted back to draft immediately
  under the explicit repair instruction. Reverified draft before committing.
- No changes to the separate trusted-admission branch or other worktrees.

## Repairs

1. CI checks the exact event base/head trees, not the index, worktree, last commit
   or merge base. Full-history checkout makes base objects available; the helper
   explicitly verifies both full nonzero SHAs are available commit objects and
   HEAD equals the event head. Missing objects fail closed. Push events use their
   exact before/after commits; missing/zero base fails closed without fallback.
   Exact-head checkout, read-only credentials, immutable action pins and the
   `always()` aggregate deriving success from all matrix jobs are retained.
2. Launcher replaces pre-reader pipe writes with bounded, rewound anonymous file
   descriptors. Before any value is written, storage must be regular and unlinked;
   unsupported hosts fail closed. Each descriptor is immediately cleanup-owned.
   Preparation/resolution/spawn failures close descriptors. A failed second spawn
   terminates/kills and reaps the first child. Consumer argv/env hold descriptor
   references only. All verification uses synthetic values, not credentials.
3. The **versioned extraction/provenance ledger** retains original source hashes,
   updates the launcher destination hash and records its adaptation. Migration
   docs accurately say it records `source_branch`, without pushing that branch.
   A read-only `git ls-remote` found no matching source branch on x-api origin.
4. Earlier candidate manifests/proofs remain historical records, not repaired-head
   clearance. Final SHA and hosted receipts go in the PR description and delivery,
   avoiding a self-referential commit identity in this versioned proof.

## Local evidence

- `make check`: PASS for the candidate: source/owner guard, complete core/shared
  adapter suites, 18 Blocks tests, nine isolation tests, six scaffold tests,
  five hygiene tests, launcher/workflow regressions, provenance and compilation.
- Targeted launcher suite: nine tests PASS, including 1 MiB before any reader,
  real inherited `/dev/fd` consumers, anonymity and 0600 checks, size rejection,
  creation/write/flush/seek/dup/short-write failures, partial preparation and
  resolution failures, both spawn failures, and kill/reap after termination timeout.
- Workflow suite: five tests PASS, including a whitespace-error mutant actually
  committed to a disposable Git repository with clean status. The old worktree
  diff passes while the new exact-base/head check rejects trailing whitespace.
  Fixing the committed defect passes. Divergent-base and shallow/missing-base
  cases demonstrate no merge-base or unavailable-base fallback.
- Explicit launcher mutants: original pipe implementation is killed by the
  five-second watchdog; removed anonymity check and removed cleanup registration
  are killed by targeted assertions. Mutation probes run only in disposable
  copies/isolated interpreters, never on the PR branch or another repository.
- Eight workflow mutants rejected: shallow history, moving base ref, wrong head,
  worktree-only whitespace check, removed `always()`, ignored aggregate result,
  persisted checkout credentials, mutable action tag.
- `make build`, `actionlint`, Python compilation, working/index whitespace checks:
  PASS. The original exact base/head committed diff also passes.
- New follow-up exact-head `make check`/build/lint/compile/diff and hosted outcomes
  must be observed after commit/push; their terminal evidence is in the delivery
  and PR body, not inferred from prior head `dadcf07` or prior CI run `34661738592`.

## Limits and unchanged boundaries

Anonymous files may be disk-backed: no RAM-only or secure-erasure claim. The
launcher remains regression-only and excluded from the standalone package. Real
cloudflared/1Password adapters, service deployment and lifecycle are not qualified
by synthetic subprocess tests. This does not change the legacy service-account
resolver environment flow. The packaged CLI still only validates manifests.

No exact-head external rail PASS, owner approval, bootstrap exception/adjudication,
merge, review-thread resolution or rereview request is claimed or performed.
Trusted admission/policy binding/API/outbox and live qualification remain later
work; nothing from the separate admission branch was imported. Main protection,
credentials, settings, deployment, enrollment and activation are untouched.
Gateway, Smoky, x-api, SMCBD/PR #3, Blocks and the live conductor were not mutated.
PR #1 must remain draft after publication for independent parent verification.
