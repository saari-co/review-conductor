# Trusted-admission Copilot constructor repair 3 — 2026-09-12

## Exact source and scope

- Repository: `saari-co/review-conductor`
- Pull request: #2, retained as draft
- Branch: `codex/trusted-admission`
- Starting and remote head: `77b88bcd7d53d7ef7e31df2e03def2dbd37a9282`
- Base: `0d1d7972c9814e113e924fc536b8839f33af9319`
- Finding: Copilot review `PRR_kwDOUXe3188AAAABNQy7AQ`
- Scope: ensure directly constructed enrollment and policy objects normalize
  unhashable repository values through `AdmissionError`.

## Repair and negative evidence

`Enrollment.__post_init__` and `AdmittedPolicy.__post_init__` now require a
string repository before dictionary membership lookup. A list supplied through
direct construction therefore reaches the documented fail-closed
`AdmissionError` boundary rather than leaking `TypeError`.

The existing direct-construction regression now covers an unhashable repository
for both dataclasses. Before the source repair it reproduced both `TypeError`
leaks. A disposable-copy mutant removed the two string guards, asserted exactly
two old membership expressions were restored, and was rejected by the focused
regression. The mutant did not touch this worktree.

## Verification before commit

Captured at `2026-09-12T03:17:52Z` with CPython 3.14.6:

- Focused direct-construction regression — PASS
- Trusted-admission suite — 18/18 PASS
- Constructor-guard mutation test — PASS

Full local checks, exact committed base/head whitespace validation, hosted CI
and another fresh Copilot review remain required on the resulting exact head.
No prior CI or review result is transferred to this repair.

## Boundaries

- No registry scope, policy identity, binding identity, workflow, governance,
  packaging or deployment behavior changed.
- No review thread was manually resolved.
- No credential, App setting, enrollment activation, target repository, branch
  protection, merge or adjudication state changed.
