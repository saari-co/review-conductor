# Issue #687 slice 1 repair — terminal notification eligibility — 2026-09-17

## Ownership

- Repository: `saari-co/review-conductor`.
- Isolated worktree:
  `/Users/cp-1/.openclaw/worktrees/0dbf576623060113/review-conductor-issue-687-routing-v1`
- Branch: `openclaw/review-conductor-issue-687-routing-v1`
- Required start/base: `8cc4094e88c8cd04c5b7da28e0c6a9ef054ea69c` (`origin/main`).
- Parent candidate / authorized repair HEAD:
  `46246e28286269699f75545789d95e2faab57489`.
- Sole source-changing owner: this worktree. No x-api, Suite, or other
  repository was edited.
- Mode: source mutation only. No merge, deploy, activation, credential,
  protection, live notification, or target-repository onboarding.

A commit cannot contain its own SHA. `candidate-manifest.json` records the
required start SHA, the parent candidate SHA, and per-file source hashes. The
branch head after this commit is the exact candidate SHA for later CI and
owner review.

## Defect and required behavior

Parent review of PR #18 required one bounded follow-up. This packet repairs
that exact head without adding a second notification system, live adapter,
activation, or x-api dispatch.

1. Representable fail-closed enrollment, unknown state/result, and equivalent
   invalid orchestration states keep routing and dispatch suppressed
   (`review_dispatch=false`, `legacy_dispatch=false`) and become eligible for
   the blocked notification path. The existing queue creates and delivers that
   message through the fake notifier; it does not raise or silently pass.
   Malformed inputs still raise. Unenrolled-none and silent repair rounds stay
   silent.
2. `repair_cycle` is the saturating ledger for the first two broad automatic
   repair rounds, not a ceiling on later imperative fixes. At cycle 2,
   `required_fix` creates a scoped `repair.route`, may change the head, reruns
   exact-head rails, preserves cycle=2/ledger, and leaves later findings in
   adjudication without another broad automatic round or ledger reset.
   `human_gate` / `defer` / `reject_false_positive` and human-only merge stay
   intact.
3. Terminal notification text is exactly
   `<repo>#<pr> ready to merge <url>` or
   `<repo>#<pr> blocked — <specific reason> <url>`. Transcript, progress,
   cycle, tier, and proof copy are removed.
4. Legacy-only output uses `route=legacy_xapi` and
   `legacy_xapi_handoff_required`. Conductor does not import, call, or dispatch
   the x-api conveyor (`legacy_dispatch` is false). Dual enrollment still
   selects Review Conductor with no duplicate legacy dispatch.

The versioned outcome contract remains
`review-conductor.orchestration-outcome.v1`. Field semantics are compatible:
`legacy_dispatch` is now constantly false, fail-closed representable states use
`notification.eligibility=blocked`, and `repair.cycle` saturates at 2.

## Bounded refusals

- No x-api checkout, SHA pin, transport, or legacy-path change.
- No merge, deploy, credential, protection, live send, or enrollment of a
  target repository.
- No automatic merge and no notification during silent repair rounds.
- No second notification system.

## Local commands and results

All commands ran in this worktree against the candidate source. Fixture PASS
is not a deployed review PASS.

| Command | Result |
| --- | --- |
| `python3 tests/test_orchestration_outcome.py` | `Ran 20 tests` / `OK`, including precise mutants |
| `python3 tests/test_review_conductor.py` | `review conductor integration tests passed` |
| `python3 tests/test_review_conductor_userland.py` | passed (25), including fail-closed blocked notify and exact concise text |
| `python3 tests/test_review_conductor_activation.py` | passed |
| `python3 tests/test_review_conductor_profiles.py` | passed |
| `make check` | recorded after the candidate commit |
| `make build` | recorded after the candidate commit |
| `python3 scripts/check_whitespace.py 8cc4094e88c8cd04c5b7da28e0c6a9ef054ea69c <new-head>` | recorded after the candidate commit |

Exact-head whitespace is `scripts/check_whitespace.py` against
`8cc4094e88c8cd04c5b7da28e0c6a9ef054ea69c` and the post-commit HEAD. That
script requires the checkout to equal HEAD, so it runs after the candidate
commit.

## Untouched boundaries

- No credentials, live databases, checkouts, or proof stores outside this
  Git-only packet.
- No x-api, spark-dgx, ClawSweeper, OpenClaw, or target-repository source.
- No review rail was requested from this packet itself. Hosted CI and owner
  review remain later exact-head work. This PR does not merge or activate.

## Remaining issue

Owner review of the repaired exact head. CI green is not external review
clearance. No merge, deploy, or live notification is requested.
