# Issue #687 frozen post-cap closure — Copilot 5242559787 nested authority — 2026-09-17

## Ownership

- Repository: `saari-co/review-conductor`.
- Isolated worktree:
  `/Users/cp-1/.openclaw/worktrees/0dbf576623060113/review-conductor-issue-687-routing-v1`
- Branch: `openclaw/review-conductor-issue-687-routing-v1`
- Required start/base: `8cc4094e88c8cd04c5b7da28e0c6a9ef054ea69c` (`origin/main`).
- Authorized frozen starting HEAD / parent candidate:
  `bc87f93c3534f1cd877fc6bb424d254b5445e055`.
- Live retained branch/PR head this closure starts from:
  `bc87f93c3534f1cd877fc6bb424d254b5445e055`.
- Sole source-changing owner: this worktree. No x-api, Suite, or other
  repository was edited.
- Mode: frozen post-cap closure of the already-reviewed Copilot review
  5242559787. This is not a new review cycle. The prior four required-fix
  findings remain closed at `bc87f93c3534f1cd877fc6bb424d254b5445e055`.
  This batch closes the remaining nested Enrollment / LegacyXapiMarker
  virtual-authority finding. No Copilot, OpenClaw, or ClawSweeper review
  is requested. No merge, deploy, activation, credential, protection,
  live notification, or target-repository onboarding.

A commit cannot contain its own SHA. `candidate-manifest.json` records the
required start SHA, the parent candidate SHA, and per-file source hashes. The
branch head after this commit is the exact candidate SHA for later CI.

## Historical evidence preserved

The previous post-cap batch at `bc87f93c3534f1cd877fc6bb424d254b5445e055`
closed these four Copilot 5242559787 required-fix findings and remains
part of the review ledger:

1. Missing, partial, or legacy pending decision identities retire
   fail-closed. Only an exact current schema, route, reason, and
   notification may remain eligible. Identical current decisions stay
   deduped.
2. Closed-state handling stays first. Unknown state, unknown rail
   results, and state/rail/result incoherence are validated after that
   closed exception and before unenrolled, legacy, or broken enrollment
   short-circuits.
3. Direct state/rail/result tuple validation requires each state's
   valid rail. Impossible tuples return blocked fail-closed with
   `review_dispatch=false`.
4. Persisted-row reconstruction maps inconsistent stored state/rail
   pairs to typed unknown results so the queue can notify blocked once.
   Direct public contract inputs remain strict.

Hosted CI for that exact head passed as run 35290354503.

## Defect and required behavior

Rebuilding only the outer Registry left nested Admission values virtual.
`Registry.__post_init__` accepts Enrollment / LegacyXapiMarker subclasses
and reads overridden attributes, so an Enrollment whose stored
repository or IDs are out of scope, or a LegacyXapiMarker whose stored
status is invalid, could still appear admitted through
`__getattribute__`. `trusted_enrollment_from_registry` now snapshots and
reconstructs each nested Enrollment and optional LegacyXapiMarker from
its own exact stored base-dataclass fields, revalidates those values
into exact base-class objects, and fails closed on subclasses, missing
fields, extra fields, or mutated stored fields. Omitted `legacy_xapi`
remains legitimate absence. Unrelated routing behavior is unchanged.

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
  packet. This is not a new review cycle.

## Local commands and results

All commands ran in this worktree against the candidate source. Fixture PASS
is not a deployed review PASS.

| Command | Result |
| --- | --- |
| focused nested Enrollment virtual-authority test | PASS; stored out-of-scope repository/IDs that appear admitted only via overridden `__getattribute__` return broken, as do subclass, extra, missing, and mutated stored fields |
| focused nested LegacyXapiMarker virtual-authority test | PASS; stored invalid marker values that appear present only via overridden `__getattribute__` return broken; omitted marker stays absent |
| focused load_registry-driven route coverage | PASS; conductor, legacy-only, none, dual, and broken routes unchanged |
| precise nested snapshot/reconstruction mutant | PASS; bypassing `_nested_registry_values_from_stored_fields` makes the Enrollment virtual-authority test fail by returning `present` |
| `python3 tests/test_orchestration_outcome.py` | 32 tests OK |
| `python3 tests/test_review_conductor_userland.py` | 36 passed |
| `python3 tests/test_trusted_admission.py` | 21 tests OK |
| `python3 -m unittest discover -s tests -p 'test_service_runtime.py'` | 79 tests OK, including the new nested negatives, prior registry-subclass regressions, and precise mutants |
| `python3 -m compileall -q tools tests scripts` | passed |
| `make check` | passed, including provenance for 14 extracted files and `git diff --check` |
| `make build` | passed; wrote `dist/review-conductor.pyz` |
| `python3 scripts/check_whitespace.py 8cc4094e88c8cd04c5b7da28e0c6a9ef054ea69c <new-head>` | recorded after the candidate commit |

Exact-head whitespace is `scripts/check_whitespace.py` against
`8cc4094e88c8cd04c5b7da28e0c6a9ef054ea69c` and the post-commit HEAD. That
script requires the checkout to equal HEAD, so it runs after the candidate
commit.

Provenance destination hashes were re-verified for the 14 recorded
extracted files. This closure does not claim an x-api source branch or
commit beyond that existing ledger.

## Untouched boundaries

- No credentials, live databases, checkouts, or proof stores outside this
  Git-only packet.
- No x-api, spark-dgx, ClawSweeper, OpenClaw, or target-repository source.
- The review ledger is closed for Copilot 5242559787. No further automatic
  review is requested from this packet. Hosted CI remains later exact-head
  work. This PR stays draft/open and does not merge or activate.

## Remaining issue

Exact-head hosted CI for the new SHA. CI green is not external review
clearance. No merge, deploy, live notification, or additional review
request is made from this packet.
