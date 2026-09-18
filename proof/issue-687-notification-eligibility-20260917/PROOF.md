# Issue #687 frozen post-cap repair — Copilot 5242559787 required-fix — 2026-09-17

## Ownership

- Repository: `saari-co/review-conductor`.
- Isolated worktree:
  `/Users/cp-1/.openclaw/worktrees/0dbf576623060113/review-conductor-issue-687-routing-v1`
- Branch: `openclaw/review-conductor-issue-687-routing-v1`
- Required start/base: `8cc4094e88c8cd04c5b7da28e0c6a9ef054ea69c` (`origin/main`).
- Authorized frozen starting HEAD / parent candidate:
  `cf797381e2fbf0630f253491742b6ed74da60168`.
- Live retained branch/PR head this reopened batch starts from:
  `cf797381e2fbf0630f253491742b6ed74da60168`.
- Sole source-changing owner: this worktree. No x-api, Suite, or other
  repository was edited.
- Mode: frozen post-cap source mutation only. Copilot review 5242559787
  authorized exactly four required-fix findings. The review ledger is
  exhausted. No Copilot, OpenClaw, or ClawSweeper review is requested.
  No merge, deploy, activation, credential, protection, live
  notification, or target-repository onboarding.

A commit cannot contain its own SHA. `candidate-manifest.json` records the
required start SHA, the parent candidate SHA, and per-file source hashes. The
branch head after this commit is the exact candidate SHA for later CI.

## Defect and required behavior

This frozen batch repairs the four verified Copilot 5242559787 findings
inside the already-approved issue #687 notification-eligibility contract.

1. Pending rows written by the previous queue format have no
   `orchestration_outcome` and use the old event key. Missing, partial,
   or legacy decision identities retire fail-closed. Only an exact
   current schema, route, reason, and notification may remain eligible.
   Identical current decisions stay deduped, so an upgrade cannot
   deliver both the old verbose row and the new concise decision.
2. Closed-state handling stays first. Unknown state, unknown rail
   results, and state/rail/result incoherence are validated after that
   closed exception and before unenrolled, legacy, or broken enrollment
   short-circuits. A contradictory unenrolled or legacy-only tuple
   produces one fail-closed blocked decision without dispatch.
3. Direct state/rail/result tuple validation requires each state's
   valid rail. Impossible tuples such as `ci_failed` with
   `rail="clawsweeper"` or `openclaw_failed` with no rail return
   blocked fail-closed with `review_dispatch=false`.
4. Persisted-row reconstruction applies the same rail-aware validation
   and maps inconsistent stored state/rail pairs to typed unknown
   results so the canonical decision can fail closed and notify once.
   Direct public contract inputs remain strict.

Preserved: versioned `review-conductor.orchestration-outcome.v1`, silent
first/second automatic rounds, saturating `repair_cycle=2`, no Conductor
legacy dispatch, human-only merge, closed-first silence, pending
revalidation, human_gate precedence, notification identity, producer-only
schema combinations, Conductor-wins dual, legacy-only handoff,
unenrolled-none silence, broken fail-closed, and x-api runtime
independence. No second notification system and no live adapters.

## Bounded refusals

- No x-api checkout, SHA pin, transport, or legacy-path change.
- No merge, deploy, credential, protection, live send, or enrollment of a
  target repository.
- No automatic merge and no notification during silent repair rounds.
- No second notification system.
- No Copilot, OpenClaw, or ClawSweeper review is requested from this
  packet.

## Local commands and results

All commands ran in this worktree against the candidate source. Fixture PASS
is not a deployed review PASS.

| Command | Result |
| --- | --- |
| `python3 tests/test_orchestration_outcome.py` | 32 tests OK, including unenrolled/legacy inconsistent-tuple fail-closed, mismatched-rail `review_dispatch=false`, persisted-row unknown mapping, and precise mutants |
| `python3 tests/test_review_conductor_userland.py` | 36 passed, including the confirmed legacy-pending duplicate-delivery case, invalid-tuple persisted rows, and unenrolled inconsistent-tuple blocked notify without dispatch |
| `python3 tests/test_trusted_admission.py` | recorded after the candidate source |
| `python3 tests/test_service_runtime.py` selected registry-enrollment and related tick tests | recorded after the candidate source |
| `python3 tests/test_review_conductor.py` | recorded after the candidate source |
| `python3 tests/test_review_conductor_activation.py` | recorded after the candidate source |
| `python3 tests/test_review_conductor_profiles.py` | recorded after the candidate source |
| `python3 -m compileall -q tools tests scripts` | recorded after the candidate source |
| `make check` | recorded after the candidate source hashes |
| `make build` | recorded after the candidate source hashes |
| `python3 scripts/check_whitespace.py 8cc4094e88c8cd04c5b7da28e0c6a9ef054ea69c <new-head>` | recorded after the candidate commit |

Exact-head whitespace is `scripts/check_whitespace.py` against
`8cc4094e88c8cd04c5b7da28e0c6a9ef054ea69c` and the post-commit HEAD. That
script requires the checkout to equal HEAD, so it runs after the candidate
commit.

## Untouched boundaries

- No credentials, live databases, checkouts, or proof stores outside this
  Git-only packet.
- No x-api, spark-dgx, ClawSweeper, OpenClaw, or target-repository source.
- The review ledger is exhausted. No further automatic review is requested
  from this packet. Hosted CI remains later exact-head work. This PR stays
  draft/open and does not merge or activate.

## Remaining issue

Exact-head hosted CI for the new SHA. CI green is not external review
clearance. No merge, deploy, live notification, or additional review
request is made from this packet.
