# Issue #687 bounded repair — Copilot 5243170006 — 2026-09-17

## Ownership

- Repository: `saari-co/review-conductor`.
- Isolated worktree:
  `/Users/cp-1/.openclaw/worktrees/0dbf576623060113/review-conductor-issue-687-routing-v1`
- Branch: `openclaw/review-conductor-issue-687-routing-v1`
- Required start/base: `8cc4094e88c8cd04c5b7da28e0c6a9ef054ea69c` (`origin/main`).
- Authorized frozen starting HEAD / parent candidate:
  `15903f321520e1e7fd263923d2580f219e0bfe83`.
- Live retained branch/PR head this repair starts from:
  `15903f321520e1e7fd263923d2580f219e0bfe83`.
- Sole source-changing owner: this worktree. No x-api, Suite, or other
  repository was edited.
- Mode: bounded repair of the three verified required findings from
  Copilot review 5243170006. No Copilot, OpenClaw, or ClawSweeper
  review is requested. No merge, deploy, activation, credential,
  protection, live notification, or target-repository onboarding.

A commit cannot contain its own SHA. `candidate-manifest.json` records the
required start SHA, the parent candidate SHA, and per-file source hashes. The
branch head after this commit is the exact candidate SHA for later CI.

## Historical evidence preserved

Prior PR #18 batches remain part of the review ledger, including the
Copilot 5242972219 nested-authority, claim/send, and worker-route
closure at `15903f321520e1e7fd263923d2580f219e0bfe83` (hosted CI run
35294022255).

## Defects and required behavior

1. `Registry.lookup` still accepted `isinstance(repository, str)`. A
   hostile `str` subclass whose `__eq__` always returns true could
   look up a real enrollment. Lookup now requires exact built-in
   strings and integers on the caller and stored enrollment fields
   and fails closed on subclasses.

2. The claim/send fence still had a gap after
   `claimed_notification_still_current` returned and before
   `notifier.send`. The already-committed `uncertain` claim is now
   followed by a `BEGIN IMMEDIATE` write reservation that revalidates
   the current decision and holds that reservation across transport.
   A webhook transition after the unlocked predicate returns retires
   the stale notification. Identical current decisions still deliver
   exactly once.

3. A long-lived `GitHubAppClient` retained its Conductor authority
   guard after the registry moved to `fail_closed`. The broken route
   queued its blocked alert, then `deliver_notifications` rejected it
   with the stale guard and restored pending. The guard is now
   installed for a live Conductor route and cleared on every other
   route transition so a Conductor-to-broken tick delivers the
   fail-closed alert exactly once.

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
| focused hostile-subclass lookup | PASS; `str`/`int` subclasses cannot synthesize `Registry.lookup` authority; exact builtins still resolve |
| focused notification after-predicate race | PASS; webhook `ci_failed` → `openclaw_queued` after the unlocked predicate retires the stale row; identical current decisions deliver once and then dedupe |
| focused same-client Conductor-to-broken transition | PASS; the retained client clears its Conductor guard and delivers the fail-closed blocked alert exactly once |
| precise mutants | PASS; lookup `isinstance`, skipped reserved claim/send revalidation, skipped full claim/send fence, and retained stale Conductor guard are killed |
| `python3 tests/test_orchestration_outcome.py` | 32 tests OK |
| `python3 tests/test_review_conductor_userland.py` | 38 passed |
| `python3 tests/test_trusted_admission.py` | 23 tests OK |
| `python3 -m unittest discover -s tests -p 'test_service_runtime.py'` | 86 tests OK |
| `python3 -m compileall -q tools tests scripts` | PASS |
| `make check` | PASS |
| `make build` | PASS; wrote `dist/review-conductor.pyz` |
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
