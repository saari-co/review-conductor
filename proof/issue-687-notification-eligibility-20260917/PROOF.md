# Issue #687 slice 1 repair 2 — closed, enrollment, fail-closed reason — 2026-09-17

## Ownership

- Repository: `saari-co/review-conductor`.
- Isolated worktree:
  `/Users/cp-1/.openclaw/worktrees/0dbf576623060113/review-conductor-issue-687-routing-v1`
- Branch: `openclaw/review-conductor-issue-687-routing-v1`
- Required start/base: `8cc4094e88c8cd04c5b7da28e0c6a9ef054ea69c` (`origin/main`).
- Parent candidate / authorized repair HEAD:
  `0d77f96d09c723790385b892eb3dd0a795e2642e`.
- Prior parent in this lineage:
  `46246e28286269699f75545789d95e2faab57489`.
- Sole source-changing owner: this worktree. No x-api, Suite, or other
  repository was edited.
- Mode: source mutation only. No merge, deploy, activation, credential,
  protection, live notification, or target-repository onboarding.

A commit cannot contain its own SHA. `candidate-manifest.json` records the
required start SHA, the parent candidate SHA, and per-file source hashes. The
branch head after this commit is the exact candidate SHA for later CI.

## Defect and required behavior

Owner adjudication of Copilot review `5240583476` on PR #18 kept the two
round-2 comments as `reject_false_positive` and required this bounded
follow-up. This packet repairs only those three remaining blockers.

1. `closed` and `closed_merged` are terminal non-dispatchable states. Direct
   `decide_orchestration_outcome` and the service-loop queue/tick/drain paths
   keep `review_dispatch=false` and `legacy_dispatch=false` and do not notify.
2. `run_tick` resolves trusted/service-owned enrollment and passes it into
   the existing notification queue. Review Conductor, legacy-only, dual,
   unenrolled, and broken semantics are runtime behavior. Caller payloads
   cannot grant enrollment. Missing row enrollment fails closed as broken,
   not Conductor-present. Conductor does not import or dispatch x-api
   (`legacy_dispatch` stays false).
3. Fail-closed blocked copy uses the canonical decision reason. Stale
   persisted blocker text cannot mask `unknown state` or
   `ambiguous or broken enrollment`. Ordinary blocked states may still use
   the current row blocker. Terminal text stays concise.

Preserved from the parent head: versioned
`review-conductor.orchestration-outcome.v1`, silent first/second automatic
rounds, saturating `repair_cycle=2` with scoped `required_fix`, no Conductor
legacy dispatch, human-only merge, rail suppression, and concise
ready/blocked copy. No second notification system and no live adapters.

## Bounded refusals

- No x-api checkout, SHA pin, transport, or legacy-path change.
- No merge, deploy, credential, protection, live send, or enrollment of a
  target repository.
- No automatic merge and no notification during silent repair rounds.
- No second notification system.
- No third review request.

## Local commands and results

All commands ran in this worktree against the candidate source. Fixture PASS
is not a deployed review PASS.

| Command | Result |
| --- | --- |
| `python3 tests/test_orchestration_outcome.py` | `Ran 23 tests` / `OK`, including closed, trusted-enrollment, and precise mutants |
| `python3 tests/test_review_conductor_userland.py` | passed (28), including closed dispatch, run_tick enrollment routes, and stale-blocker canonical copy |
| `python3 tests/test_review_conductor.py` | `review conductor integration tests passed` |
| `python3 tests/test_review_conductor_activation.py` | `review conductor shared adapter tests passed` |
| `python3 tests/test_review_conductor_profiles.py` | `Ran 31 tests` / `OK` |
| `python3 -m compileall -q tools tests scripts` | passed |
| `make check` | passed, including provenance verification and `git diff --check` |
| `make build` | passed; wrote `dist/review-conductor.pyz` |
| `python3 scripts/check_whitespace.py 8cc4094e88c8cd04c5b7da28e0c6a9ef054ea69c <new-head>` | recorded after the candidate commit |

Exact-head whitespace is `scripts/check_whitespace.py` against
`8cc4094e88c8cd04c5b7da28e0c6a9ef054ea69c` and the post-commit HEAD. That
script requires the checkout to equal HEAD, so it runs after the candidate
commit.

## Untouched boundaries

- No credentials, live databases, checkouts, or proof stores outside this
  Git-only packet.
- No x-api, spark-dgx, ClawSweeper, OpenClaw, or target-repository source.
- No review rail was requested from this packet itself. Hosted CI remains
  later exact-head work. This PR stays draft/open and does not merge or
  activate.

## Remaining issue

Exact-head hosted CI for the new SHA. CI green is not external review
clearance. No merge, deploy, live notification, or review request is made
from this packet.
