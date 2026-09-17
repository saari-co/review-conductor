# Issue #687 frozen post-cap repair — Copilot 5242352644 required-fix — 2026-09-17

## Ownership

- Repository: `saari-co/review-conductor`.
- Isolated worktree:
  `/Users/cp-1/.openclaw/worktrees/0dbf576623060113/review-conductor-issue-687-routing-v1`
- Branch: `openclaw/review-conductor-issue-687-routing-v1`
- Required start/base: `8cc4094e88c8cd04c5b7da28e0c6a9ef054ea69c` (`origin/main`).
- Authorized frozen starting HEAD / parent candidate:
  `10c8e5edf051766ccb5b112899dbd02078324a0e`.
- Live retained branch/PR head this reopened batch starts from:
  `4e3faa1ed7a934118d5a42b67de0403483bf849e` (published descendant of
  `10c8e5e`; no reset or force-push).
- Sole source-changing owner: this worktree. No x-api, Suite, or other
  repository was edited.
- Mode: frozen post-cap source mutation only. Copilot review 5242352644
  authorized exactly four required-fix findings. No Copilot, OpenClaw, or
  ClawSweeper review is requested. No merge, deploy, activation,
  credential, protection, live notification, or target-repository
  onboarding.

A commit cannot contain its own SHA. `candidate-manifest.json` records the
required start SHA, the parent candidate SHA, and per-file source hashes. The
branch head after this commit is the exact candidate SHA for later CI.

## Defect and required behavior

This frozen batch repairs the four verified Copilot 5242352644 findings
inside the already-approved issue #687 notification-eligibility contract.

1. `trusted_enrollment_from_registry` snapshots and revalidates the exact
   base `Registry` dataclass fields before deriving a route. Subclass
   methods, properties, and synthetic attributes cannot synthesize
   Conductor, legacy, dual, or broken routing. `load_registry` remains
   the producer of those stored fields.
2. The service-profile `github_app.repository` must be an exact
   `INITIAL_ENROLLMENT_SCOPE` string before omitted-marker absence is
   treated as legitimate none/legacy-absent. Missing, non-string,
   out-of-scope, or malformed profiles fail closed.
3. The persisted-row adapter maps inconsistent stored state/rail data
   (including `waiting_human` with a missing rail) to typed unknown
   results. The canonical decision returns blocked fail-closed, and the
   existing queue enqueues/sends the concise operator notification
   exactly once. Direct public contract inputs remain strict.
4. The complete state/rail/result tuple is validated before any
   `review_dispatch` or notification eligibility is calculated.
   Impossible contradictions, including `ci_running` plus OpenClaw
   findings and `ready_for_human_merge` plus a non-ClawSweeper rail,
   return blocked fail-closed and never dispatch or merge-ready.

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
| `python3 tests/test_trusted_admission.py` | recorded after the candidate source |
| `python3 tests/test_orchestration_outcome.py` | recorded after the candidate source |
| `python3 tests/test_service_runtime.py` selected registry-enrollment, subclass-override, malformed-profile, and related tick tests | recorded after the candidate source |
| `python3 tests/test_review_conductor_userland.py` | recorded after the candidate source |
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
- No further automatic review is requested from this packet. Hosted CI
  remains later exact-head work. This PR stays draft/open and does not
  merge or activate.

## Remaining issue

Exact-head hosted CI for the new SHA. CI green is not external review
clearance. No merge, deploy, live notification, or additional review
request is made from this packet.
