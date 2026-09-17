# Issue #687 slice 1 repair 3 — closed-first, tick gate, pending revalidation — 2026-09-17

## Ownership

- Repository: `saari-co/review-conductor`.
- Isolated worktree:
  `/Users/cp-1/.openclaw/worktrees/0dbf576623060113/review-conductor-issue-687-routing-v1`
- Branch: `openclaw/review-conductor-issue-687-routing-v1`
- Required start/base: `8cc4094e88c8cd04c5b7da28e0c6a9ef054ea69c` (`origin/main`).
- Parent candidate / authorized repair HEAD:
  `a5ecd640cf957f298a789440c9d3756b4c084c61`.
- Prior parent in this lineage:
  `0d77f96d09c723790385b892eb3dd0a795e2642e`.
- Sole source-changing owner: this worktree. No x-api, Suite, or other
  repository was edited.
- Mode: source mutation only. No merge, deploy, activation, credential,
  protection, live notification, or target-repository onboarding.

A commit cannot contain its own SHA. `candidate-manifest.json` records the
required start SHA, the parent candidate SHA, and per-file source hashes. The
branch head after this commit is the exact candidate SHA for later CI.

## Defect and required behavior

Owner adjudication of Copilot review `5241356417` on PR #18 kept the two
already-adjudicated false positives and authorized this bounded post-cap
repair plus one subsequent Copilot review. This packet repairs the three
inline findings and the two suppressed schema/manifest items.

1. `closed` and `closed_merged` stay silent and non-dispatchable even when
   trusted enrollment is broken. Closed-state handling precedes enrollment
   short-circuits; both dispatch flags remain false.
2. `run_tick` resolves trusted/service-owned enrollment at tick start and
   suppresses hydration, action draining, result collection, and review
   stages unless the trusted route is `review_conductor`. Dual enrollment
   selects Review Conductor. Legacy-only, unenrolled, and broken routes are
   real runtime behavior. Conductor never dispatches x-api.
3. Pending notification rows are revalidated against the current head and
   trusted enrollment before claim or send. Close, supersession, or an
   enrollment-route change retires ineligible rows. Still-eligible
   fail-closed blocked notifications send; stale ready/blocked rows do not.
4. `notification.eligibility` no longer includes unsupported `fail_closed`.
   Canonical representable failures keep `route=fail_closed` and use
   `blocked`.
5. The changed `Makefile` and its SHA-256 are recorded in
   `candidate-manifest.json`.

Preserved from the parent head: versioned
`review-conductor.orchestration-outcome.v1`, silent first/second automatic
rounds, saturating `repair_cycle=2` with scoped `required_fix`, no Conductor
legacy dispatch, human-only merge, rail suppression, concise ready/blocked
copy, and x-api runtime independence. No second notification system and no
live adapters.

## Bounded refusals

- No x-api checkout, SHA pin, transport, or legacy-path change.
- No merge, deploy, credential, protection, live send, or enrollment of a
  target repository.
- No automatic merge and no notification during silent repair rounds.
- No second notification system.
- No review request from this packet. Parent orchestrator owns the next
  Copilot dispatch.

## Local commands and results

All commands ran in this worktree against the candidate source. Fixture PASS
is not a deployed review PASS.

| Command | Result |
| --- | --- |
| `python3 tests/test_orchestration_outcome.py` | `Ran 25 tests` / `OK`, including closed-before-enrollment, fail_closed eligibility removal, and precise mutants |
| `python3 tests/test_review_conductor_userland.py` | passed (31), including closed+broken silence, run_tick stage suppression, and pending-notification revalidation |
| `python3 tests/test_review_conductor.py` | `review conductor integration tests passed` |
| `python3 tests/test_review_conductor_activation.py` | `review conductor shared adapter tests passed` |
| `python3 tests/test_review_conductor_profiles.py` | `Ran 31 tests` / `OK` |
| `python3 tests/test_service_runtime.py` | superseded pending rows retire before authority; precise mutant skips revalidation |
| `python3 -m compileall -q tools tests scripts` | passed |
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
- No review rail was requested from this packet itself. Hosted CI remains
  later exact-head work. This PR stays draft/open and does not merge or
  activate.

## Remaining issue

Exact-head hosted CI for the new SHA, then the parent-owned Copilot review
of that exact head. CI green is not external review clearance. No merge,
deploy, live notification, or review request is made from this packet.
