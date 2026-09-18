# Issue #687 bounded repair — Copilot 5242972219 — 2026-09-17

## Ownership

- Repository: `saari-co/review-conductor`.
- Isolated worktree:
  `/Users/cp-1/.openclaw/worktrees/0dbf576623060113/review-conductor-issue-687-routing-v1`
- Branch: `openclaw/review-conductor-issue-687-routing-v1`
- Required start/base: `8cc4094e88c8cd04c5b7da28e0c6a9ef054ea69c` (`origin/main`).
- Authorized frozen starting HEAD / parent candidate:
  `a3922f0af5984756bc9deb69d43775827ac40c38`.
- Live retained branch/PR head this repair starts from:
  `a3922f0af5984756bc9deb69d43775827ac40c38`.
- Sole source-changing owner: this worktree. No x-api, Suite, or other
  repository was edited.
- Mode: bounded repair of the three verified required findings from
  Copilot review 5242972219. The fourth finding (cycle-2 "unbounded
  repairs") is REJECTED as inconsistent with the confirmed product
  contract. No Copilot, OpenClaw, or ClawSweeper review is requested.
  No merge, deploy, activation, credential, protection, live
  notification, or target-repository onboarding.

A commit cannot contain its own SHA. `candidate-manifest.json` records the
required start SHA, the parent candidate SHA, and per-file source hashes. The
branch head after this commit is the exact candidate SHA for later CI.

## Historical evidence preserved

Prior PR #18 batches remain part of the review ledger, including the
Copilot 5242559787 nested-authority closure at
`a3922f0af5984756bc9deb69d43775827ac40c38` (hosted CI run 35291533204).

## Defects and required behavior

1. Nested trusted registry fields still admitted `str` subclasses.
   `Enrollment` and `LegacyXapiMarker` now require exact built-in
   strings (and exact integers) for every authority-bearing field.
   Snapshot reconstruction copies those exact base values and fails
   closed on subclasses, missing fields, extra fields, or mutated
   stored fields. A `str` subclass with attacker-controlled equality
   cannot synthesize repository, ID, or legacy authority.

2. Notification eligibility was not bound to the later send. The
   claim/send fence now revalidates the expected current head state
   and complete canonical orchestration decision immediately before
   transport. A webhook that advances the same tuple between
   eligibility and send retires the stale notification. Identical
   current decisions still dedupe and deliver exactly once.

3. `service_entrypoint.registry_provider` applied
   `require_profile_enrolled` before `run_service_tick` could derive
   legacy-only, unenrolled, dual, or broken routes. External registry
   document validation/loading is now separate from strict Conductor
   ingress admission. The production worker can represent all trusted
   routes. Webhook ingress remains enrolled-only. Malformed documents
   fail closed. Only Conductor/dual dispatch reviews. There is no
   x-api dispatch.

4. REJECTED: Copilot 5242972219's cycle-2 "unbounded repairs" finding
   is inconsistent with the confirmed product contract. The saturating
   2/2 automatic ledger and explicit human `required_fix` gate for
   later scoped repairs are preserved. Existing
   `test_repair_cycle_saturates_and_required_fix_stays_scoped` and the
   mutants that restore the old ceiling or increment past two remain
   the truthful rejection evidence.

Preserved: versioned `review-conductor.orchestration-outcome.v1`, silent
first/second automatic rounds, saturating `repair_cycle=2`, no Conductor
legacy dispatch, human-only merge, closed-first silence, pending
revalidation, human_gate precedence, notification identity, producer-only
schema combinations, Conductor-wins dual, legacy-only handoff,
unenrolled-none silence, broken fail-closed, omitted-marker backward
compatibility, load_registry-driven route coverage, and x-api runtime
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
| focused nested exact-builtin string/ID negatives | PASS; str/int subclasses cannot synthesize Enrollment or LegacyXapiMarker authority; direct construction and stored-field reconstruction fail closed |
| focused notification claim/send race | PASS; webhook `ci_failed` → `openclaw_queued` between eligibility and send retires the stale row; identical current decisions deliver once and then dedupe |
| focused entrypoint-loaded route coverage | PASS; Conductor and dual dispatch reviews; legacy-only/none skip review stages; broken fails closed; no x-api dispatch; malformed documents fail closed; ingress stays enrolled-only |
| precise mutants | PASS; direct-construction isinstance, reconstruction isinstance conversion, skipped claim/send fence, and re-added provider enrollment gate are killed |
| rejected cycle-2 evidence | PASS; saturating 2/2 ledger and scoped `required_fix` mutants remain green |
| `python3 tests/test_orchestration_outcome.py` | 32 tests OK |
| `python3 tests/test_review_conductor_userland.py` | 37 passed |
| `python3 tests/test_trusted_admission.py` | 22 tests OK |
| `python3 -m unittest discover -s tests -p 'test_service_runtime.py'` | recorded after the full check |
| `python3 -m compileall -q tools tests scripts` | recorded after the full check |
| `make check` | recorded after the full check |
| `make build` | recorded after the full check |
| `python3 scripts/check_whitespace.py 8cc4094e88c8cd04c5b7da28e0c6a9ef054ea69c <new-head>` | recorded after the candidate commit |

Exact-head whitespace is `scripts/check_whitespace.py` against
`8cc4094e88c8cd04c5b7da28e0c6a9ef054ea69c` and the post-commit HEAD. That
script requires the checkout to equal HEAD, so it runs after the candidate
commit.

Provenance destination hashes were re-verified for the 14 recorded
extracted files. This repair does not claim an x-api source branch or
commit beyond that existing ledger.

## Untouched boundaries

- No credentials, live databases, checkouts, or proof stores outside this
  Git-only packet.
- No x-api, spark-dgx, ClawSweeper, OpenClaw, or target-repository source.
- No Copilot, OpenClaw, or ClawSweeper review is requested from this
  packet. Hosted CI remains later exact-head work. This PR stays
  draft/open and does not merge or activate.

## Remaining issue

Exact-head hosted CI for the new SHA. CI green is not external review
clearance. No merge, deploy, live notification, or additional review
request is made from this packet.
