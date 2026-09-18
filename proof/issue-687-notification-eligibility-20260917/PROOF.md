# Issue #687 Copilot 5243850325 bounded repair — 2026-09-17

## Ownership

- Repository: `saari-co/review-conductor`.
- Isolated worktree:
  `/Users/cp-1/.openclaw/worktrees/0dbf576623060113/review-conductor-issue-687-routing-v1`
- Branch: `openclaw/review-conductor-issue-687-routing-v1`
- Required start/base: `8cc4094e88c8cd04c5b7da28e0c6a9ef054ea69c` (`origin/main`).
- Authorized frozen starting HEAD / parent candidate:
  `a6bcff0cf973748bb21b20990195f14c75ce9d85`.
- Live retained branch/PR head this repair starts from:
  `a6bcff0cf973748bb21b20990195f14c75ce9d85`.
- Sole source-changing owner: this worktree. No x-api, Suite, or other
  repository was edited.
- Mode: one bounded repair pass for the verified registry-send-window
  blocker in Copilot review `5243850325`. No Copilot, OpenClaw, or
  ClawSweeper review is requested. No merge, deploy, activation,
  credential, protection, live notification, or target-repository
  onboarding.

A commit cannot contain its own SHA. `candidate-manifest.json` records the
required start SHA, the parent candidate SHA, and per-file source hashes. The
branch head after this commit is the exact candidate SHA for later CI.

## Historical evidence preserved

Prior PR #18 batches remain part of the review ledger, including the
reservation-specific `BEGIN IMMEDIATE` send-lock proof at
`8f03e512b57a925e270ea7b14d495002555c9294` and the Copilot 5243575441
unhashable-input, quality-flag, and reserved-route-freshness closure at
`a6bcff0cf973748bb21b20990195f14c75ce9d85`. The confirmed saturating
2/2 automatic-repair ledger, post-cap `required_fix` contract, and prior
adjudications are unchanged. Copilot 5242972219's cycle-2 "unbounded
repairs" finding remains rejected. Review `5243850325` is the repaired
finding for this packet.

## Verified blocker repaired

`BEGIN IMMEDIATE` still serializes SQLite state writes through
transport. It does not, and does not now claim to, serialize arbitrary
OS-level replacement of an external registry file. The reserved send
boundary now holds a versioned service-owned registry/route lease
through `notifier.send`. The supported `RegistryRouteLease.replace`
contract fail-closes while that hold is active, so a cooperating
registry replacement cannot occur between final trusted-route
validation and transport. Identical current decisions still send
exactly once. Userland callers without a lease keep the previous API.
This is not a guarantee against nonconforming OS-level writes.

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
| focused registry-lease unit | PASS; send hold pins the generation and blocks supported replace; replace works after release |
| focused supported-replace-during-send | PASS; cooperating replace is blocked across `notifier.send`; blocked copy still sends once; identical current decisions stay deduped |
| focused reserved enrollment-route-change retire | PASS; route change after the unlocked predicate still retires |
| focused reservation-specific race | PASS; `BEGIN IMMEDIATE` still blocks a competing state write through transport |
| focused lease-bypass mutant | PASS; removing `hold_registry_send_lease` lets supported replace succeed during send and the intended test fails |
| `python3 tests/test_orchestration_outcome.py` | 34 tests OK |
| `python3 tests/test_review_conductor_userland.py` | 40 passed |
| `python3 -m compileall -q tools tests scripts` | recorded after the candidate source is complete |
| `make check` | recorded after the candidate source is complete |
| `make build` | recorded after the candidate source is complete |
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
