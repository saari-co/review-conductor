# Issue #687 frozen post-cap repair — human gate, identity, registry enrollment, schema — 2026-09-17

## Ownership

- Repository: `saari-co/review-conductor`.
- Isolated worktree:
  `/Users/cp-1/.openclaw/worktrees/0dbf576623060113/review-conductor-issue-687-routing-v1`
- Branch: `openclaw/review-conductor-issue-687-routing-v1`
- Required start/base: `8cc4094e88c8cd04c5b7da28e0c6a9ef054ea69c` (`origin/main`).
- Authorized frozen starting HEAD / parent candidate:
  `10c8e5edf051766ccb5b112899dbd02078324a0e`.
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

Owner adjudication of Copilot review `5241986535` on PR #18 kept the already
adjudicated false positives and authorized this frozen post-cap
`required_fix` batch. The automatic-repair ledger remains saturated at
cycle 2. This packet repairs the four required invariants.

1. `human_gate=true` takes precedence. No structurally valid input with
   that gate may produce `merge_ready` or silently continue review
   dispatch. Closed heads stay silent and non-dispatchable. Direct
   regressions cover terminal-ready and nonterminal states.
2. Notification event identity includes canonical decision route, reason,
   and eligibility. A stale pending row retires while the new blocked
   decision enqueues and delivers exactly once. Identical decisions remain
   deduped.
3. `service_runtime.run_service_tick` wires registry-owned enrollment into
   `run_tick` from the authoritative registry/admission result. Userland
   `enabled` / `blockers` flags do not select legacy, none, dual, or
   broken routes. Bounded service-runtime regressions cover conductor,
   legacy-only, none, dual, and broken/ambiguous routes, with no review
   dispatch off the Review Conductor route and fail-closed behavior where
   required.
4. `contracts/orchestration-outcome.schema.json` accepts only
   producer-emittable notification eligibility/kind/channel combinations.
   Positive producer samples and negative impossible combinations are
   covered.

Preserved from the parent head: versioned
`review-conductor.orchestration-outcome.v1`, silent first/second automatic
rounds, saturating `repair_cycle=2` with scoped `required_fix`, no Conductor
legacy dispatch, human-only merge, rail suppression, concise ready/blocked
copy, closed-first silence, pending revalidation, and x-api runtime
independence. No second notification system and no live adapters.

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
| `python3 tests/test_orchestration_outcome.py` | recorded after the candidate source |
| `python3 tests/test_review_conductor_userland.py` | recorded after the candidate source |
| `python3 tests/test_service_runtime.py` selected registry-enrollment, identity, and related tick tests | recorded after the candidate source |
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
