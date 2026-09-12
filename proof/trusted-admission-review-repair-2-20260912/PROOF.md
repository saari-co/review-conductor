# Trusted-admission Copilot regression repair 2 — 2026-09-12

## Exact source and scope

- Repository: `saari-co/review-conductor`
- Pull request: #2, retained as draft
- Branch: `codex/trusted-admission`
- Starting and remote head: `f5be10adca415df95a17396366696851e02753cd`
- Base: `0d1d7972c9814e113e924fc536b8839f33af9319`
- Finding: Copilot comment `3994894972`
- Scope: replace one vacuous deterministic-binding assertion with a meaningful
  equivalent-direct-construction regression. No production code changed.

## Repair and negative evidence

The former assertion compared one digest with another digest after appending
`"x"`; the length difference made it pass regardless of deterministic binding.
The replacement constructs a distinct but field-equivalent `Admission` and
compares the two binding IDs directly.

A disposable-copy mutant added the admission object's runtime identity to the
binding payload. The new dedicated regression rejected that nondeterministic
implementation. The mutation setup asserted the injected `id(self)` field was
present before running the test and did not touch this worktree.

## Verification before commit

Captured at `2026-09-12T03:11:18Z` with CPython 3.14.6:

- Dedicated equivalent-construction regression — PASS
- Trusted-admission suite — 18/18 PASS

Full local checks, exact committed base/head whitespace validation and hosted CI
remain required on the resulting exact head. Fresh Copilot review is separately
required. No prior CI or review result is transferred to this repair.

## Boundaries

- No implementation, policy, profile, packaged output, workflow or governance
  behavior changed.
- No review thread was manually resolved.
- No deployment, credential, App setting, enrollment activation, target
  repository, branch protection, merge or adjudication state changed.
