# Issue #687 slice 1 — terminal notification eligibility — 2026-09-17

## Ownership

- Repository: `saari-co/review-conductor`.
- Isolated worktree:
  `/Users/cp-1/.openclaw/worktrees/0dbf576623060113/review-conductor-issue-687-routing-v1`
- Branch: `openclaw/review-conductor-issue-687-routing-v1`
- Required start/base: `8cc4094e88c8cd04c5b7da28e0c6a9ef054ea69c` (`origin/main`).
- Sole source-changing owner: this worktree. No x-api, Suite, or other
  repository was edited.
- Mode: source mutation only. No merge, deploy, activation, credential,
  protection, live notification, or target-repository onboarding.

A commit cannot contain its own SHA. `candidate-manifest.json` records the
required start SHA and per-file source hashes. The branch head after this
commit is the exact candidate SHA for later CI and owner review.

## Defect and required behavior

The legacy notification queue treated every `TERMINAL_STATES` row as
queueable and sent special copy for `awaiting_adjudication` and
`repair_required`. That notified during the first two automatic repair
rounds and conflated internal progression with genuinely terminal human
action.

`decide_orchestration_outcome` is now the canonical versioned decision
(`review-conductor.orchestration-outcome.v1`). The existing
`queue_notifications` consumer uses that outcome. There is no second
notification system and no x-api runtime dependency.

- Silent: nonterminal work, `repair_required`, and `awaiting_adjudication`
  without a genuine human gate, including the first two automatic rounds
  and third-set dispositions that have not reached `waiting_human`.
- Merge-ready: both required exact-head rails effectively clean, including
  authorized deferrals/rejections on an unchanged head, plus the existing
  ready-quality policy.
- Blocked: `waiting_human`, `ci_failed`, `openclaw_failed`, and
  `clawsweeper_failed`.
- Suppression: OpenClaw findings block ClawSweeper eligibility; ClawSweeper
  findings block merge-ready eligibility.
- Enrollment: Review Conductor wins dual enrollment and forbids duplicate
  legacy dispatch; legacy-only stays untouched; neither enrollment is
  no-review/no-notification; broken enrollment fails closed.
- A changed head preserves the 2/2 repair ledger. Unknown or malformed
  outcomes fail closed.

## Bounded refusals

- No x-api checkout, SHA pin, transport, or legacy-path change.
- No merge, deploy, credential, protection, live send, or enrollment of a
  target repository.
- No automatic merge and no notification during silent repair rounds.

## Local commands and results

All commands ran in this worktree against the candidate source. Fixture PASS
is not a deployed review PASS.

| Command | Result |
| --- | --- |
| `python3 tests/test_orchestration_outcome.py` | `Ran 19 tests` / `OK` |
| `python3 tests/test_review_conductor_userland.py` | `review conductor userland tests passed (24)` |
| `make check` | passed, including userland `(24)`, orchestration `19`, provenance `14 files`, `py_compile`, and `git diff --check` |
| `make build` | wrote `dist/review-conductor.pyz` |
| `python3 scripts/check_provenance.py` | `extraction hashes verified (14 files); Python compilation passed` |
| `git diff --check` | clean |

Exact-head whitespace is `scripts/check_whitespace.py` against
`8cc4094e88c8cd04c5b7da28e0c6a9ef054ea69c` and the post-commit HEAD. That
script requires the checkout to equal HEAD, so it runs after the candidate
commit.

## Untouched boundaries

- No credentials, live databases, checkouts, or proof stores outside this
  Git-only packet.
- No x-api, spark-dgx, ClawSweeper, OpenClaw, or target-repository source.
- No review rail was requested from this packet itself. Hosted CI and owner
  review remain later exact-head work. This PR does not merge or activate.

## Recommended next bounded #687 slice

Define the stable versioned provider job/result contract that later replaces
prose finding counts, feeding the same `openclaw_result` /
`clawsweeper_result` fields this outcome already consumes.
