# Issue #687 frozen post-cap repair — registry legacy_xapi contract — 2026-09-17

## Ownership

- Repository: `saari-co/review-conductor`.
- Isolated worktree:
  `/Users/cp-1/.openclaw/worktrees/0dbf576623060113/review-conductor-issue-687-routing-v1`
- Branch: `openclaw/review-conductor-issue-687-routing-v1`
- Required start/base: `8cc4094e88c8cd04c5b7da28e0c6a9ef054ea69c` (`origin/main`).
- Authorized frozen starting HEAD / parent candidate:
  `10c8e5edf051766ccb5b112899dbd02078324a0e`.
- Live retained branch/PR head this reopened batch starts from:
  `f4ef1aad93accd03001ae0db0920091c2a059ef4` (published descendant of
  `10c8e5e`; no reset or force-push).
- Sole source-changing owner: this worktree. No x-api, Suite, or other
  repository was edited.
- Mode: frozen post-cap source mutation only. Copilot review ledger is
  exhausted at 2/2. No Copilot, OpenClaw, or ClawSweeper review is
  requested. No merge, deploy, activation, credential, protection, live
  notification, or target-repository onboarding.

A commit cannot contain its own SHA. `candidate-manifest.json` records the
required start SHA, the parent candidate SHA, and per-file source hashes. The
branch head after this commit is the exact candidate SHA for later CI.

## Defect and required behavior

This reopened frozen batch repairs one newly found registry-contract gap
inside the already-approved post-cap invariants. The Copilot ledger remains
exhausted at 2/2. No further review is requested.

The versioned external service-owned registry
(`review-conductor.enrollment.v2`) now represents an optional exact-profile
`legacy_xapi` marker. Existing documents that omit the field stay valid and
mean legacy absent (backward-compatible Conductor-only behavior). The marker
may appear only as a top-level object with exact keys `repository` and
`status`, exact `INITIAL_ENROLLMENT_SCOPE` placement, and status
`present` or `absent`. Strings, booleans, lists, `null`, `broken`, unknown
repositories, missing or extra keys, and enrollment-nested copies fail
closed at `load_registry`.

`trusted_enrollment_from_registry` consumes only that validated loaded
field for the running service profile. Synthetic subclass attributes and
userland `enabled` / `blockers` cannot grant or select a route. Dual is
Conductor present plus this profile's loaded marker. Legacy-only reports
`route=legacy_xapi` / handoff required while `legacy_dispatch` stays false.
Non-Conductor service ticks do not dispatch review stages. Broken or
ambiguous enrollment remains fail-closed.

Preserved: versioned `review-conductor.orchestration-outcome.v1`, silent
first/second automatic rounds, saturating `repair_cycle=2`, no Conductor
legacy dispatch, human-only merge, closed-first silence, pending
revalidation, human_gate precedence, notification identity, producer-only
schema combinations, and x-api runtime independence. No second notification
system and no live adapters.

## Bounded refusals

- No x-api checkout, SHA pin, transport, or legacy-path change.
- No merge, deploy, credential, protection, live send, or enrollment of a
  target repository.
- No automatic merge and no notification during silent repair rounds.
- No second notification system.
- No Copilot, OpenClaw, or ClawSweeper review is requested. The 2/2
  ledger is exhausted. This is a frozen post-cap repair batch.

## Local commands and results

All commands ran in this worktree against the candidate source. Fixture PASS
is not a deployed review PASS.

| Command | Result |
| --- | --- |
| `python3 tests/test_trusted_admission.py` | recorded after the candidate source |
| `python3 tests/test_orchestration_outcome.py` | recorded after the candidate source |
| `python3 tests/test_service_runtime.py` selected registry-enrollment, malformed-marker, identity, and related tick tests | recorded after the candidate source |
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
request is made from this packet. The Copilot ledger is exhausted at 2/2.
