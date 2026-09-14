# GitHub review integration completion — 2026-09-14

## Ownership

- Fresh sole implementer requested by Bobby for this bounded completion.
- Conductor worktree:
  `/Users/cp-1/Developer/worktrees/review-conductor-openclaw-github-review-integration-completion-20260914`
- Conductor branch: `openclaw/github-review-integration-completion-20260914`
- Conductor start/base: `8a60ffc5c2764ff001bf5b3db40d670963d0f428`
- Suite worktree:
  `/Users/cp-1/Developer/worktrees/review-source-openclaw-github-review-integration-completion-20260914`
- Suite branch: `openclaw/github-review-integration-completion-20260914`
- Suite start/base: `d50ec1671418aaf3ee1aef2020c9f2d20e4b6a18`
- Other worktrees and owners were left untouched. No review request, merge,
  protection change, runtime edit, or additional delegation.

## Qualified boundary

Conductor now classifies native review content separately from merge
authorization, publishes useful rail-check output, and plans one idempotent
GitHub projection comment plus owned ClawSweeper status labels through an
injected API. Suite source adds an executable no-dual-writer cutover preview
with exact before/after protected-check selectors. No live write, credential,
webhook, or protection mutation is performed here.

Suite #19 at `ed1c53d69ea9a798e330bf7eb71e9a401feb81b5` remains the original
genuine full-rail evidence: native `keep_open`, Conductor
`waiting_human` / `action_required`. That history is not rewritten.

## Local required checks

- Conductor `make check` and `make build` passed on this source.
- Suite `npm run check`, `npm test`, `npm run preview:cutover`, and actionlint
  passed on the paired suite worktree.

## Source-bound tests

- `tests/test_review_result_projection.py` covers clean/findings/failure/human
  policy/proof-deficiency separation, own-current-check handling, stale and
  mismatched artifact rejection, duplicate-comment idempotency, foreign-label
  preservation, and meaningful check links.
- Userland fixture `test_keep_open_without_defects_is_review_success_not_merge`
  proves a completed keep-open report with non-blocking proof becomes review
  success while merge stays human-only.
- Suite `npm run preview:cutover` and `check-suite-integration` prove writer-fence
  stages, exact App `15368` / `4916376` selectors, rollback order, and `--apply`
  refusal.

## Finite cutover preview

Before (current required writers): `CI@15368`, `OpenClaw Review Rail@15368`,
`ClawSweeper Review Rail@15368`.

After authorized rebind/cutover: `CI@15368`, `OpenClaw Review Rail@4916376`,
`ClawSweeper Review Rail@4916376`.

Stages: current → rails-fenced → protection-rebind → cutover-complete.
Rollback is the reverse. CI never leaves Actions. Placeholders stay fail-closed
until that separately authorized rebind. No permanent bootstrap bypass.

## Out of scope

Deployment, live credentials, runtime restart, webhook change, branch-protection
mutation, merge, label/comment publication on existing test/product PRs, new
review request, Blocks 9443, suite 9444 runtime, product stacks, and ClawSweeper
#18.
