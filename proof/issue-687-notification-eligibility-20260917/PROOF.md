# Issue #687 proof-only continuation — reservation-specific send lock — 2026-09-17

## Ownership

- Repository: `saari-co/review-conductor`.
- Isolated worktree:
  `/Users/cp-1/.openclaw/worktrees/0dbf576623060113/review-conductor-issue-687-routing-v1`
- Branch: `openclaw/review-conductor-issue-687-routing-v1`
- Required start/base: `8cc4094e88c8cd04c5b7da28e0c6a9ef054ea69c` (`origin/main`).
- Authorized frozen starting HEAD / parent candidate:
  `51034ad821de3f0aafd5c194b184490b23b9fd61`.
- Live retained branch/PR head this continuation starts from:
  `51034ad821de3f0aafd5c194b184490b23b9fd61`.
- Sole source-changing owner: this worktree. No x-api, Suite, or other
  repository was edited. The prior Cursor session is settled and was not
  resumed.
- Mode: proof-only continuation of the already-authorized notification
  race fix. No source design change. No Copilot, OpenClaw, or ClawSweeper
  review is requested. No merge, deploy, activation, credential,
  protection, live notification, or target-repository onboarding.

A commit cannot contain its own SHA. `candidate-manifest.json` records the
required start SHA, the parent candidate SHA, and per-file source hashes. The
branch head after this commit is the exact candidate SHA for later CI.

## Historical evidence preserved

Prior PR #18 batches remain part of the review ledger, including the
Copilot 5243170006 hostile-lookup, reserved revalidation, and stale-guard
closure at `51034ad821de3f0aafd5c194b184490b23b9fd61` (hosted CI run
35296009486).

## Independently identified proof gap

The claim/send fence already uses `BEGIN IMMEDIATE` and holds that SQLite
write reservation through transport. Existing tests and mutants proved the
extra reserved revalidation, but they did not independently prove that the
reservation blocks a competing state write after the final predicate.

This continuation adds one deterministic reservation-specific negative
regression. After the final reserved revalidation, a second writer is
coordinated during `notifier.send`. That writer cannot take `BEGIN
IMMEDIATE` or commit a `heads` mutation while transport holds the
reservation. After send returns and the reservation commits/releases, the
same mutation commits. Crash-survival, exact-once delivery of the reserved
row, and all prior #687 contracts are unchanged.

A precise disposable-copy mutant weakens only
`connection.execute("BEGIN IMMEDIATE")` to `BEGIN` while retaining the extra
`claimed_notification_still_current` revalidation. That exact regression
fails because the competing writer can reserve and commit during send.

Source was not changed. The fence already had the required reservation.

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
| focused reservation-specific race | PASS; competing `BEGIN IMMEDIATE` / `heads` mutation cannot commit after the final reserved revalidation until transport returns and the reservation releases; the reserved row still sends exactly once |
| focused weaken-`BEGIN IMMEDIATE` mutant | PASS; extra revalidation retained; intended test fails because the second writer reserved or committed during send |
| focused related race/crash regressions | PASS; after-predicate retire, between-eligibility-and-send retire, and uncertain-before-transport crash-survival remain |
| `python3 tests/test_orchestration_outcome.py` | 32 tests OK |
| `python3 tests/test_review_conductor_userland.py` | 38 passed |
| `python3 tests/test_trusted_admission.py` | 23 tests OK |
| `python3 -m unittest discover -s tests -p 'test_service_runtime.py'` | 87 tests OK |
| `python3 -m compileall -q tools tests scripts` | PASS |
| `make check` | PASS; precise mutants include the reserved-revalidation skip and the reservation-only weaken |
| `make build` | PASS; wrote `dist/review-conductor.pyz` |
| `python3 scripts/check_whitespace.py 8cc4094e88c8cd04c5b7da28e0c6a9ef054ea69c <new-head>` | recorded after the candidate commit |

Exact-head whitespace is `scripts/check_whitespace.py` against
`8cc4094e88c8cd04c5b7da28e0c6a9ef054ea69c` and the post-commit HEAD. That
script requires the checkout to equal HEAD, so it runs after the candidate
commit.

Provenance destination hashes were re-verified for the 14 recorded
extracted files. This continuation does not claim an x-api source branch or
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
