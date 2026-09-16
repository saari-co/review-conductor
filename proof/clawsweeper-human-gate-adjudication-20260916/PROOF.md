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
