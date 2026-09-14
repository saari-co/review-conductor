# GitHub review integration completion — 2026-09-14

## Ownership

- Replacement Cursor implementer for the frozen 2026-09-14T22:05Z parent
  acceptance repair. Same assigned worktrees and draft PRs.
- Conductor worktree:
  `/Users/cp-1/Developer/worktrees/review-conductor-openclaw-github-review-integration-completion-20260914`
- Conductor branch: `openclaw/github-review-integration-completion-20260914`
- Conductor start/base: `8a60ffc5c2764ff001bf5b3db40d670963d0f428`
- Suite worktree:
  `/Users/cp-1/Developer/worktrees/review-source-openclaw-github-review-integration-completion-20260914`
- Suite branch: `openclaw/github-review-integration-completion-20260914`
- Suite start/base: `d50ec1671418aaf3ee1aef2020c9f2d20e4b6a18`
- Other worktrees and owners were left untouched. No review request, merge,
  protection change, runtime edit, live publication, or additional delegation.

## Qualified boundary

Conductor now revalidates exact-tuple authority on every projection mutation,
owns comments only when the trusted App authored an exact marker, paginates
comment/label lists safely, and reconciles an uncertain create to one owned
summary. Classification preserves legacy-qualified clean results and fails
closed on ambiguous `keep_open` without typed process gates. Each rail check
binds its own stage/result/artifact and never uses the reviewed PR as
`details_url`. Suite source adds an exact inert guard-retirement patch plus
rollback validation; the live placeholder workflow still writes failing rail
names. No live write, credential, webhook, or protection mutation is
performed here.

Suite #19 at `ed1c53d69ea9a798e330bf7eb71e9a401feb81b5` remains the original
genuine full-rail evidence: native `keep_open`, Conductor
`waiting_human` / `action_required`. That history is not rewritten.

## Local required checks

- Conductor `make check` and `make build` on this source.
- Suite `npm run check`, `npm test`, `npm run preview:cutover`, and actionlint
  on the paired suite worktree.

## Source-bound tests

- `tests/test_review_result_projection.py` covers clean/findings/failure/human
  policy/proof-deficiency separation, own-current-check handling, stale and
  mismatched artifact rejection, duplicate-comment idempotency, foreign-label
  preservation, meaningful check links, ambiguous `keep_open` without typed
  gates, copied-marker human comments, head-change update of one owned
  summary, real-client exact-tuple authority on comment POST, uncertain-create
  reconciliation, safe comment pagination, and per-rail OpenClaw vs
  ClawSweeper report binding.
- Userland fixture `test_keep_open_without_defects_is_review_success_not_merge`
  still requires explicit typed process gates before review-success.
- Suite `npm run preview:cutover` and `check-suite-integration` prove writer-fence
  stages, exact App `15368` / `4916376` selectors, rollback order, `--apply`
  refusal, and that the committed placeholder still writes rail names while
  `review/adapters/guard-retirement.generated.yml` is the inert retirement body.

## Finite cutover preview

Before (current required writers): `CI@15368`, `OpenClaw Review Rail@15368`,
`ClawSweeper Review Rail@15368`.

After authorized rebind/cutover: `CI@15368`, `OpenClaw Review Rail@4916376`,
`ClawSweeper Review Rail@4916376`.

Stages: current → rails-fenced → protection-rebind → cutover-complete.
Rollback is the reverse. CI never leaves Actions. Placeholders stay fail-closed
and still write the required rail names until that separately authorized
rebind applies the generated retirement patch. No permanent bootstrap bypass.

## Remaining concrete dependency

Native producer reports that use `decision=keep_open` for the circular
own-check / owner-merge wait must emit an explicit typed `process_gates`
array containing `own_current_check` (and `owner_merge_authority` when that
is the remaining gate). Conductor will not infer those gates from a missing
or empty field, an `F` rating, or `keep_open` alone. No producer source was
changed here.

## Out of scope

Deployment, live credentials, runtime restart, webhook change, branch-protection
mutation, merge, label/comment publication on existing test/product PRs, new
review request, Blocks 9443, suite 9444 runtime, product stacks, and ClawSweeper
#18.
