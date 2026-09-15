# Draft-to-native terminal handoff

## Assignment and source

- Purpose: consume a genuine native terminal after draft OpenClaw clearance;
  do not rerun reviewers or manufacture their verdicts.
- Lane / writable owner: `suite24_publication_diagnosis`; final closer: parent
  coordinator. Mode: source-only mutate; runtime, deployment and merge remain
  parent-owned.
- Repository: `saari-co/review-conductor`.
- Fresh fetched base: `84966d938f7b63f044c86ff07a510ac102aa754c` (`origin/main`).
- Isolated branch: `codex/draft-native-terminal-handoff-20260915`.
- Scope: core handoff, bridge-only recovery, production-path synthetic tests and
  extraction provenance. Held PR #11 is not incorporated.
- Stop: tested committed draft PR and exact-head CI; no review request, merge,
  deployment, database repair, credentials, protection mutation or model rerun.

## Diagnosis and change

The ready-for-review branch retained an OpenClaw request ID while changing the
active rail to ClawSweeper. A later genuine native terminal was consequently
rejected as a different running request. A materialized quality/report row was
not an accepted terminal; publication correctly remained gated on terminal
state, although its report metadata could appear alongside queued status.

The prospective transition now clears the prior rail's request ID, matching
the existing non-draft OpenClaw-to-ClawSweeper transition.

The existing terminal bridge can recover this precise prior-version condition:

1. The genuine native terminal artifact, proof digest, exact tuple/epoch,
   dispatched action and pending successful workflow are checked normally.
2. Recovery applies only to a non-draft, queued ClawSweeper head; never a running
   request or a different rail.
3. The retained ID must equal its dispatched same-epoch OpenClaw action's request.
4. An accepted, current-epoch OpenClaw terminal is revalidated as authoritative,
   comprehensive P3, exact tuple, clean and for that same request. An accepted
   same-tuple ready event must follow that terminal.
5. Clearing the residual ID, recording `clawsweeper.draft_handoff_recovered`,
   accepting the native terminal and binding its workflow share one transaction.
   Any later refusal rolls the whole transaction back. Duplicate native bridge
   calls retain the existing idempotency behavior.

Wrong native run IDs and running-request mismatches still fail closed. Legacy
profiles without generalized review policy are not given recovery behavior.

## Reproduction and verification

The unchanged base was reproduced using the production fixture path:
draft -> CI -> OpenClaw clean -> ready -> native dispatch -> native terminal.
Its error was `ClawSweeper terminal workflow_run_id does not match the running request`.
No live mutation was used for the reproduction.

- `python3 tests/test_review_conductor_profiles.py`: **30 tests pass**.
- Five new tests cover complete draft-to-terminal ingestion; recovery of seeded
  old state using the unchanged artifact; duplicate ingestion and a single audit;
  wrong/running/other-rail IDs; stale, mistyped and untrusted OpenClaw evidence;
  missing accepted ready evidence; wrong native tuple/run/digest; and rollback
  following a downstream rejection.
- Successful synthetic terminals project both review success and readiness for
  a human merge decision, with `merge_authorized: false`.
- `make check`: **PASS**, including governance, engine/runtime/publication,
  Blocks/profile regressions, transport/supervisor, provenance and whitespace.
- `make build`: **PASS**.
- Hosted exact-head CI: pending publication.

Tested source SHA-256:

| Source | SHA-256 |
| --- | --- |
| `tools/review_conductor.py` | `2a22c7e558e92256ab725f3657303450d734d2be347c4901dfdd9841df974ba9` |
| `tools/review_conductor_runtime.py` | `e2975f922a8ac83a4e9949be8cd6ad2302615e781f4ca9aafb01ba1777c5fe34` |
| `tests/test_review_conductor_profiles.py` | `5b43c35d3c0c1afcc539a55290127e954630e5d72c49ed780d961be3d650e261` |

## Executable recovery plan (parent-owned, not executed here)

After source qualification and separately authorized deployment, keep the
existing service profile, source checkout, state root and proof inbox. Its
normal `run_tick` invokes `drain_bridge_inboxes` before collecting or dispatching
anything; the existing unreceipted native terminal is its input. No new command,
DB patch, webhook redelivery, reviewer request or synthetic terminal is needed.

Observe the existing tick accepting that artifact, writing one recovery audit
and the native terminal event, binding the workflow as `verdict_ingested`, and
writing the normal bridge receipt. The same receipt's digest must equal the
unchanged terminal artifact bytes. The report digest must remain unchanged.
Repeat observation must show the receipt/idempotency fence, not a new review.

Then verify actual check completion, original evidence links, owned summary and
status labels using the ordinary publication path. These live effects are not
claimed by the synthetic regression results. No direct state edit is part of
this plan, and merge authority remains human-only.

## Limitations

No live recovery or GitHub publication was performed by this lane. The existing
skipped-to-queued UI issue and unrelated failed-run attribution work in PR #11
remain separate; this patch does not introduce that PR's unbound-failure logic.
No credentials, real reports, production database contents or private runtime
configuration are committed here.
