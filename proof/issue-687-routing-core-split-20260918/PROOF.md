# Issue #687 PR #18 routing/admission core split — 2026-09-18

## Ownership

- Repository: `saari-co/review-conductor`.
- Isolated worktree:
  `/Users/cp-1/.openclaw/worktrees/0dbf576623060113/review-conductor-issue-687-routing-v1`
- Branch: `openclaw/review-conductor-issue-687-routing-v1`
- Required start/base: `8cc4094e88c8cd04c5b7da28e0c6a9ef054ea69c` (`origin/main`).
- Authorized pre-split full candidate / parent:
  `6eaf99ef11180ea523db3229c2e5c49c736bdd64`.
- Preservation branch, already pushed at that exact SHA:
  `openclaw/review-conductor-pr18-full-presplit-20260918`.
- Sole source-changing owner: this Cursor ACP worktree. No x-api, Suite, or
  other repository was edited.
- Mode: authorized split of existing draft PR #18. The existing PR is reduced
  to an independently mergeable deterministic routing/state/admission core.
  Notification delivery/authority is deferred to a stacked adapter PR. No
  Copilot, OpenClaw, or ClawSweeper review is requested. No merge, deploy,
  activation, credential, protection, live notification, or target-repository
  onboarding.

A commit cannot contain its own SHA. `candidate-manifest.json` records the
required start SHA, the preserved parent candidate SHA, and per-file source
hashes. The branch head after this commit is the exact reduced-core SHA for
later CI.

## Historical evidence preserved

Prior PR #18 batches remain part of the review ledger and are not reset by
this split. That includes the reservation-specific `BEGIN IMMEDIATE`
send-lock proof at `8f03e512b57a925e270ea7b14d495002555c9294`, the Copilot
5243575441 closure at `a6bcff0cf973748bb21b20990195f14c75ce9d85`, and the
Copilot 5243850325 registry-send-window lease proof at
`6eaf99ef11180ea523db3229c2e5c49c736bdd64`. The historical packet
`proof/issue-687-notification-eligibility-20260917/` remains exact-head
truthful for that preserved full candidate. The confirmed saturating 2/2
automatic-repair ledger, post-cap `required_fix` contract, and prior
adjudications are unchanged. Copilot 5242972219's cycle-2 "unbounded
repairs" finding remains rejected. Copilot review `5243987118` findings
are adapter defects and are moved out of this reduced diff.

## Retained in reduced PR #18

- Versioned `review-conductor.orchestration-outcome.v1`.
- Exact five enrollment states from trusted registry bytes: Conductor-only,
  legacy-only, none, dual (Conductor wins), and broken/malformed fail-closed.
- Conductor never dispatches legacy x-api. Legacy-only is handoff required
  only. No enrollment means no review dispatch and no notification.
- Outcome-driven notification eligibility: first two broad rounds silent;
  `repair_cycle=2` saturates the automatic ledger; explicit post-cap
  `required_fix` remains allowed; merge-ready or genuinely blocked/fail-closed
  outcomes stay eligible.
- Inert/service-owned registry validation, exact enrollment/policy binding,
  durable/replay state, injected transports, and disabled profile wiring.
- Production-loader coverage through the external registry document path for
  Conductor-only, legacy-only, none, dual, and broken/malformed bytes.
- Direct public contract inputs stay strict. Persisted rows may fail closed
  through the existing typed row adapter that already belongs in core.
- Rail suppression and human-only merge. No activation.

## Deferred to the stacked adapter PR

- Notification send leases and reserved claim/send fencing.
- Route-freshness guards used only for delivery.
- Pending-row revalidation and complete current-event identity.
- Copilot `5243987118` repairs: malformed persisted rail/state rows must
  produce one concise fail-closed blocked notification instead of aborting;
  event identity must include complete canonical schema/kind/channels/decision
  identity; retry-attempt changes must revalidate the complete current event
  key so attempt 1 and attempt 2 cannot both send.
- Live-side-effect proofs and adapter-only mutants for those delivery
  authority windows.

This reduced core does not add a second notification system and does not
execute a service or live delivery path.

## Frozen interfaces

Preserved across both slices: versioned orchestration outcome schema; exact
five enrollment states from real registry bytes; Conductor-wins dual;
legacy-only handoff required only; no enrollment means no review dispatch
and no notification; malformed/ambiguous/broken enrollment fails closed;
first two broad rounds silent; `repair_cycle=2` saturates the automatic
ledger while explicit post-cap `required_fix` remains allowed; rail
suppression and human-only merge; no activation, live delivery, credentials,
config, protection, target onboarding, or x-api mutation.

## Local commands and results

All commands ran in this worktree against the reduced candidate source.
Fixture PASS is not a deployed review PASS.

| Command | Result |
| --- | --- |
| `python3 tests/test_orchestration_outcome.py` | 34 tests OK |
| `python3 tests/test_trusted_admission.py` | 23 tests OK |
| `python3 tests/test_review_conductor_userland.py` | 34 passed |
| `python3 tests/test_service_runtime.py` | recorded with `make check` |
| `python3 tests/test_review_conductor.py` | integration tests passed |
| `python3 tests/test_review_conductor_activation.py` | shared adapter tests passed |
| `python3 tests/test_review_conductor_profiles.py` | 31 tests OK |
| `python3 -m compileall -q tools tests scripts` | recorded after the candidate source is complete |
| `make check` | recorded after the candidate source is complete |
| `make build` | recorded after the candidate source is complete |
| `python3 scripts/check_whitespace.py 8cc4094e88c8cd04c5b7da28e0c6a9ef054ea69c <new-head>` | recorded after the candidate commit |

Exact-head whitespace is `scripts/check_whitespace.py` against
`8cc4094e88c8cd04c5b7da28e0c6a9ef054ea69c` and the post-commit HEAD. That
script requires the checkout to equal HEAD, so it runs after the candidate
commit.

Provenance destination hashes were updated for the two extracted files
changed by this reduction. This split does not claim an x-api source branch
or commit beyond the existing ledger.

## Untouched boundaries

- No credentials, live databases, checkouts, or proof stores outside this
  Git-only packet.
- No x-api, spark-dgx, ClawSweeper, OpenClaw, or target-repository source.
- No Copilot, OpenClaw, or ClawSweeper review is requested from this
  packet. Hosted CI remains later exact-head work. This PR stays
  draft/open and does not merge or activate. Review threads are not
  resolved.

## Remaining issue

Exact-head hosted CI for the new reduced SHA. CI green is not external
review clearance. The stacked notification adapter remains a separate draft
PR. No merge, deploy, live notification, or additional review request is
made from this packet.
