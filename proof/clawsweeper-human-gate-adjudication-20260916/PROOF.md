# ClawSweeper human-gate owner adjudication — 2026-09-16

## Ownership

- Repository: `saari-co/review-conductor`.
- Isolated worktree:
  `/Users/cp-1/.openclaw/worktrees/0dbf576623060113/review-conductor-openclaw-adjudication-routing-20260916`
- Branch: `openclaw/review-conductor-openclaw-adjudication-routing-20260916`
- Required start/base/head: `e5824afe876b28ce8cb74e97080b1d1b1f23482b` (`origin/main`).
- Sole source-changing owner: this worktree. No other worktree was opened or
  edited, including `/Users/cp-1/Developer/review-conductor/openclaw-smcbd-suite`.
- Mode: source mutation only. No merge, deploy, activation, credential,
  protection, live-state, Suite PR #6, or review-rail request.

A commit cannot contain its own SHA. `candidate-manifest.json` records the
required start SHA and per-file source hashes. The branch head after this
commit is the exact candidate SHA for later CI and owner review.

## Defect and required behavior

A completed exact-head ClawSweeper terminal with `result=human_gate` moved the
head to `waiting_human`, but `adjudication.completed` was accepted only from
`awaiting_adjudication`. An owner could not record a completed ClawSweeper
proof-gap decision without rerunning reviewers.

For an unchanged exact repository/PR/base/head/review_epoch, a completed
ClawSweeper human-gate plus owner adjudication whose classifications contain
only `defer` and/or `reject_false_positive` now advances to
`ready_for_human_merge`. The existing ClawSweeper rail, request/workflow_run,
authoritative `reviewer_actor`, and `proof_ref` are preserved. Projection uses
the existing `effective_quality` rewrite. No OpenClaw or ClawSweeper rerun and
no new epoch are created. Merge remains human-only.

## Bounded refusals

- Stale or mismatched request, tuple, epoch, rail, or actor.
- OpenClaw `human_gate` remaining in `waiting_human`.
- `required_fix`, `human_gate`, or mixed unsupported classifications from
  ClawSweeper `waiting_human`. Existing repair routing stays on
  `awaiting_adjudication` and still caps at two cycles.
- No merge-policy override, branch-protection bypass, live state edit,
  deploy, service restart, or activation.

## Local commands and results

All commands ran in this worktree against the candidate source. Fixture PASS
is not a deployed review PASS.

| Command | Result |
| --- | --- |
| `python3 tests/test_review_conductor.py` selected proof-gap, negatives, OpenClaw fail-closed, and `test_precise_openclaw_exact_contract_mutants` | `review conductor integration tests passed` |
| `test_completed_clawsweeper_human_gate_owner_adjudication_projects_ready` and OpenClaw fail-closed in `tests/test_review_conductor_userland.py` | both passed |
| `python3 tests/test_review_result_projection.py` | `review result projection tests passed (23)` |
| `make check` | passed, including userland `(22)`, provenance `14 files`, `py_compile`, and `git diff --check` |
| `make build` | wrote `dist/review-conductor.pyz` |
| `python3 scripts/check_provenance.py` | `extraction hashes verified (14 files); Python compilation passed` |
| `git diff --check` | clean |

Exact-head whitespace is `scripts/check_whitespace.py` against
`e5824afe876b28ce8cb74e97080b1d1b1f23482b` and the post-commit HEAD. That
script requires the checkout to equal HEAD, so it runs after the candidate
commit.

## Untouched boundaries

- No credentials, live databases, checkouts, or proof stores outside this
  Git-only packet.
- No Suite source, PR #15, PR #6, protection body, or runtime registry.
- No review rail was requested. Hosted CI and owner review remain later
  exact-head work. This PR does not merge or activate.

## Inherited lifecycle-test follow-up

Hosted candidate run 35127137575 failed the inherited serving-loop SIGTERM
baseline and its precise mutant on Python 3.11 / Ubuntu. That family also
failed on exact-base run 35089876605. The repair is confined to lifecycle
fixtures/tests and is recorded in
`proof/runtime-lifecycle-sigterm-determinism-20260916/PROOF.md`. Production
stop/cleanup and this packet's waiting_human adjudication are unchanged.

## Copilot review round 1 — 2026-09-16

- Reviewer: `copilot-pull-request-reviewer[bot]`
- Review: [5226361943](https://github.com/saari-co/review-conductor/pull/17#pullrequestreview-5226361943)
- Exact submitted head: `de56bb1b9f0e68394ece8b6a29df297d251dfa46`
- Check: [35130580531](https://github.com/saari-co/review-conductor/actions/runs/35130580531)
- Ledger after this request: **1/2**. One remaining Copilot request is reserved for the repaired head.

| Finding | Disposition | Evidence |
| --- | --- | --- |
| Inline [4029129906](https://github.com/saari-co/review-conductor/pull/17#discussion_r4029129906) (`tools/review_conductor.py:1787`) | `required_fix` | `waiting_human` plus ClawSweeper rail/request also occurs after findings → `human_gate` classification and after `required_fix` at the two-cycle limit. Those rows keep a `clawsweeper.terminal` of `result=findings`. The waiting_human path now requires the exact bound terminal `result=human_gate` before `continue_after_adjudication`. |
| Suppressed body note at `tools/review_conductor.py:1802` | `defer` | `queue_notifications` still requires raw `clawsweeper_quality.ready_qualified`. That skip is the existing fail-closed gate for proof-deficient artifacts; the ready notification template also claims exact-head clean. Projection already consumes `effective_quality`. Changing notification eligibility or copy is nonblocking and out of this repair's scope. |

### Repair and local proof

`accepted_clawsweeper_terminal` selects the latest non-stale exact-tuple ClawSweeper terminal whose `workflow_run_id` matches the current request. A later `defer` / `reject_false_positive` from a findings-origin `waiting_human` row fails closed. The completed `human_gate` owner path is unchanged. Merge remains human-only; no reviewer rerun or new epoch is created.

| Command | Result |
| --- | --- |
| `python3 tests/test_review_conductor.py` selected happy-path, stale/mismatch, human_gate-terminal provenance, OpenClaw fail-closed, and `test_precise_openclaw_exact_contract_mutants` | `review conductor integration tests passed` |
| `python3 tests/test_review_conductor_userland.py` | `review conductor userland tests passed (22)` |
| `python3 tests/test_review_result_projection.py` | `review result projection tests passed (23)` |
| `make check` | passed, including provenance, `py_compile`, and `git diff --check` |
| `make build` | wrote `dist/review-conductor.pyz` |

No third Copilot review will be requested from this lane.

## Frozen post-cap source acceptance — 2026-09-16

This is source acceptance of the exact-proof binding repair only. It is not
reviewer clearance, review PASS, merge authority, or a third Copilot request.
The retained Copilot ledger remains **2/2**.

- Reviewer: `copilot-pull-request-reviewer[bot]`
- Review: [5226583017](https://github.com/saari-co/review-conductor/pull/17#pullrequestreview-5226583017)
- Inline [4029305388](https://github.com/saari-co/review-conductor/pull/17#discussion_r4029305388) (`tools/review_conductor.py:1847`)
- Exact submitted head at that review: `e4fba327eebfccd3c7997ed0e2a11479be047585`
- Disposition: `required_fix` — projected/adjudicated `clawsweeper_quality` could
  reference a later collector upsert `workflow_run_id` than the exact
  adjudicated request.

### Repair

`accepted_clawsweeper_quality` now binds any tuple/epoch quality row to
`row["review_request_id"]` before `waiting_human` `continue_after_adjudication`.
Accepted, effective, and projected ClawSweeper quality use the same exact
`workflow_run_id` bind. Mismatched quality/evidence fails closed. Matching
quality still advances defer/reject-only `waiting_human` to
`ready_for_human_merge` with no reviewer rerun, new epoch, repair/merge action,
or merge authority.

Suppressed round-2 notes (enabled-profile coverage, lifecycle marker/counter
races) remain out of this frozen scope.

### Local proof

All commands ran in this worktree against the candidate source. Fixture PASS
is not a deployed review PASS.

| Command | Result |
| --- | --- |
| `python3 tests/test_review_conductor.py` selected happy-path, stale/mismatch, human_gate-terminal provenance, mismatched quality `workflow_run_id`, OpenClaw fail-closed, and `test_precise_openclaw_exact_contract_mutants` | `review conductor integration tests passed` |
| `python3 tests/test_review_result_projection.py` selected exact-adjudication projection, proof-gap happy path, mismatched quality fail-closed, and `test_precise_quality_workflow_run_binding_mutant` | `review result projection tests passed (4)` |
| `tests/test_review_conductor_userland.py` happy-path projected quality/report `workflow_run_id` bind | passed |
| `make check` | passed, including userland `(22)`, provenance `14 files`, `py_compile`, and `git diff --check` |
| `make build` | wrote `dist/review-conductor.pyz` |
| `python3 scripts/check_provenance.py` | `extraction hashes verified (14 files); Python compilation passed` |
| `git diff --check` | clean |

Interpreters exercised: Homebrew `python3` / `python3.14` (`/opt/homebrew/opt/python@3.14/bin/python3.14`, 3.14.6) for focused tests, `make check`, `make build`, and compilation; Apple CLT `/usr/bin/python3` (3.9.6) for the focused conductor suite only. Projection import on 3.9 fails on the pre-existing runtime `tuple[...] | tuple[...]` annotation.

Exact-head whitespace is `scripts/check_whitespace.py` against
`e5824afe876b28ce8cb74e97080b1d1b1f23482b` and the post-commit HEAD. That
script requires the checkout to equal HEAD, so it runs after the candidate
commit.

No third Copilot review was requested.
