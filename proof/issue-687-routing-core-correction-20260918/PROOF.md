# Issue #687 PR #18 routing/admission core correction — 2026-09-18

## Ownership

- Repository: `saari-co/review-conductor`.
- Isolated worktree:
  `/Users/cp-1/.openclaw/worktrees/0dbf576623060113/review-conductor-issue-687-routing-v1`
- Branch: `openclaw/review-conductor-issue-687-routing-v1`
- Required start/base: `8cc4094e88c8cd04c5b7da28e0c6a9ef054ea69c` (`origin/main`).
- Authorized pre-split full candidate:
  `6eaf99ef11180ea523db3229c2e5c49c736bdd64` on
  `openclaw/review-conductor-pr18-full-presplit-20260918`.
- Published incomplete-split head this ordinary commit starts from:
  `6497a3266feffe2d81b10d57015d736e92dd9b5a`.
- Sole source-changing owner: this Cursor ACP worktree. No x-api, Suite, or
  other repository was edited.
- Mode: owner-approved forward correction of draft PR #18. Live-runtime
  consumption that leaked into the first split is removed here and remains
  deferred to the stacked adapter. No history rewrite, no force-push, no
  Copilot, OpenClaw, or ClawSweeper review request. No merge, deploy,
  activation, credential, protection, live notification, or target-repository
  onboarding.

A commit cannot contain its own SHA. `candidate-manifest.json` records the
required start SHA, the preserved parent candidate SHA, the published
incomplete-split head, and per-file source hashes. The published corrected
core SHA is `04b2f583c7cf9a6a62dd0428b2497fb55087c754`. Hosted CI
[35307282569](https://github.com/saari-co/review-conductor/actions/runs/35307282569)
passed on that exact head.

## Why this correction exists

The first split at `6497a3266feffe2d81b10d57015d736e92dd9b5a` kept
deterministic outcome/schema, enrollment helpers, and the saturating repair
ledger, but it also changed live userland queue/message/identity/delivery,
`run_tick` route suppression, `run_service_tick` enrollment wiring, and
`service_entrypoint.registry_provider`. Core import/merge must not alter live
queue, delivery, GitHub dispatch, or notifier behavior. This commit restores
those runtime entry functions to `origin/main` and keeps only the inert core.

Historical packet `proof/issue-687-routing-core-split-20260918/` remains
exact-head truthful for `6497a3266feffe2d81b10d57015d736e92dd9b5a`.

## Retained in corrected PR #18

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
  durable/replay state, and disabled profile wiring.
- `trusted_enrollment_from_registry` snapshot/revalidation helpers.
- Direct public contract inputs stay strict. Persisted rows may fail closed
  through the existing typed row adapter that already belongs in core.
- Rail suppression and human-only merge. No activation.

## Deferred to the stacked adapter PR

- `notification_message`, `notification_event_identity`,
  `queue_notifications`, and `deliver_notifications`.
- `run_tick` routing/delivery changes and `suppressed_review_stages`.
- Runtime notification authority, guards, and send leases.
- `run_service_tick` enrollment wiring and
  `service_entrypoint.registry_provider` document-only worker loading.
- Copilot `5243987118` repairs: malformed persisted rail/state rows must
  produce one concise fail-closed blocked notification instead of aborting;
  event identity must include complete canonical schema/kind/channels/decision
  identity; retry-attempt changes must revalidate the complete current event
  key.
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

All commands ran in this worktree against the corrected candidate source.
Fixture PASS is not a deployed review PASS.

| Command | Result |
| --- | --- |
| `python3 tests/test_orchestration_outcome.py` | 34 tests OK |
| `python3 tests/test_trusted_admission.py` | 23 tests OK |
| focused registry-helper and live-entry regressions | PASS: helpers remain; `run_service_tick` / worker-gate keep approved-base behavior |
| `python3 tests/test_review_conductor.py` | integration tests passed, including saturating 2/2 ledger |
| `python3 tests/test_review_conductor_userland.py` | 22 passed (approved-base live queue/delivery) |
| `python3 -m compileall -q tools tests scripts` | passed |
| `make check` | passed |
| `make build` | passed; wrote `dist/review-conductor.pyz` (untracked, not committed) |
| `python3 scripts/check_whitespace.py 8cc4094e88c8cd04c5b7da28e0c6a9ef054ea69c <new-head>` | recorded after the candidate commit |
| Hosted CI `35307282569` on `04b2f583c7cf9a6a62dd0428b2497fb55087c754` | passed; https://github.com/saari-co/review-conductor/actions/runs/35307282569 |

Exact-head whitespace is `scripts/check_whitespace.py` against
`8cc4094e88c8cd04c5b7da28e0c6a9ef054ea69c` and the post-commit HEAD. That
script requires the checkout to equal HEAD, so it runs after the candidate
commit.

Provenance destination hashes were updated for restored userland files.
This correction does not claim an x-api source branch or commit beyond the
existing ledger.

## Untouched boundaries

- No credentials, live databases, checkouts, or proof stores outside this
  Git-only packet.
- No x-api, spark-dgx, ClawSweeper, OpenClaw, or target-repository source.
- No Copilot, OpenClaw, or ClawSweeper review is requested from this
  packet. Hosted CI
  [35307282569](https://github.com/saari-co/review-conductor/actions/runs/35307282569)
  passed on exact head `04b2f583c7cf9a6a62dd0428b2497fb55087c754`. This PR
  stays draft/open and does not merge or activate. Review threads are not
  resolved.

## Remaining issue

The stacked notification adapter remains a separate draft PR and must merge
this corrected tip. Hosted CI
[35307282569](https://github.com/saari-co/review-conductor/actions/runs/35307282569)
already passed on `04b2f583c7cf9a6a62dd0428b2497fb55087c754`; CI green is
not external review clearance. No merge, deploy, live notification, or
additional review request is made from this packet.
