# Maintainer-triggered ClawSweeper rereview

Status: source-qualified Conductor behavior. Not live activation, App
subscription change, producer publication, or merge authority.

## Supported commands

Conductor admits authenticated `issue_comment.created` deliveries on pull
requests and treats only these case-insensitive mentions as rereview commands:

| Command | Role |
| --- | --- |
| `@ClawSweeper rereview` | Canonical maintainer command |
| `@clawsweeper re-review` | Documented alias |

The mention may include GitHub's `[bot]` suffix (`@ClawSweeper[bot] rereview`).
Surrounding prose is allowed; the verb must be an exact token.

## Explicit non-commands

| Text or event | Conductor behavior |
| --- | --- |
| `@clawsweeper review` | Ignored. Not a rereview alias. OpenClaw closeouts post this for first-pass review; treating it as rereview would loop and bypass ready-gating. |
| `@clawsweeper re-run` or other verbs | Ignored |
| `issue_comment.edited` or `deleted` | Ignored. Edits are not a supported trigger. |
| `pull_request.edited` | Unsupported. Body edits do not start or refresh review. |
| Non-PR issue comments | Ignored |
| Bot, reviewer-actor, or non-maintainer comments | Refused. Comment text is never authority. |

Authoritative sender permission is GitHub `author_association` in
`OWNER`, `MEMBER`, or `COLLABORATOR`, plus `user.type=User`. Reviewer actors
from the enrolled profile cannot command rereview.

## Same-head evidence refresh

When the current exact repository/PR/base/head/epoch is unchanged and
comprehensive CI plus OpenClaw are still clean, a valid command:

1. Refreshes the PR-body evidence digest observed on the comment.
2. Creates a new `clawsweeper.dispatch` attempt for that same epoch.
3. Clears the previous ClawSweeper request id and marks the prior
   `clawsweeper.dispatch`, including an already-dispatched findings attempt,
   obsolete so a cached proof-deficient terminal cannot be replayed.
4. Leaves the PR-event `source_updated_at` watermark, repair cycle,
   mutation owner, original reviewer context, and human-only merge unchanged.

A later terminal for the superseded workflow is stale.

## New head, base, or policy

A new head, base, or policy binding is a new exact tuple. Conductor will not
reuse an earlier revision's CI or OpenClaw PASS. The command waits until the
current tuple has successful comprehensive CI and clean OpenClaw. Ready-gated
first ClawSweeper dispatch remains the automatic path after those rails clear.

## Dedup, in-flight, and budgets

- Identical webhook deliveries stay replay-safe (`duplicate_delivery`).
- The same comment id is consumed once.
- `clawsweeper_queued` / `clawsweeper_running` waits; it does not start a
  second in-flight review.
- Two automatic repair cycles remain the cap. Exhausted `waiting_human` refuses
  rereview; a human must adjudicate.
- Draft PRs wait for ready-for-review when the profile requires it.

## Acknowledgement and verdict

Accepted, waiting, and authorized-refusal commands create a
`rereview.acknowledge` action naming the exact tuple and attempt or the wait /
refuse reason. Unauthorized, bot, non-PR, and non-command comments have no
public acknowledgement. An uncertain GitHub comment publish or abandoned
acknowledgement claim stays `reconcile_required` and is not retried. The new
ClawSweeper verdict publishes only through the existing rail checks and
presentation path. No result authorizes merge.

## Historical bundles and no-artifact execution failure

A success bundle is retired as historical attention only when the current
head's review epoch cannot still own it. The lookup joins each action to that
current head on `review_epoch`, and a report epoch must be the same epoch.
An earlier epoch's action stays `dispatched` across reopen and does not match.
`pending`, `preparing`, `dispatching`, and `reconcile_required` leave the
receipt pending, because dispatch remains `dispatching` until the API response
commits and a fast run can finish in that window. A receipt that stores
`workflow_run_id` binds only that run; a different run is historical. Several
current-epoch dispatches that do not uniquely exclude this run stay pending
instead of being retired. Superseded, closed, and unbound bundles still take
the historical path. Retirement writes no verdict, does not change any head,
and does not stop later runs or projection in the same tick.

A non-success run with no exact verdict bundle fails the current head only when
that run is the already-started request, the dispatch receipt stores that
workflow run id, or the single admission job log shows one consistent
`pr_number`, `expected_base_sha`, `expected_head_sha`, and `review_epoch` for
the current dispatched tuple. The same `pending`, `preparing`, `dispatching`,
and `reconcile_required` statuses leave that failure receipt pending when a
current-epoch dispatch has not stored a different `workflow_run_id`. A fast
failed run can finish before dispatch commits the receipt; retiring it in
that window would drop the later bind. A non-object dispatch receipt,
including JSON `null` or `[]`, owns no run. A present `workflow_run_id` must
be a non-boolean integer; a string or other JSON type is not that run. A
JSON object that omits `workflow_run_id` still owns the run. An empty string
or SQL NULL is not that object and owns no run. A report epoch
is at most ten digits, the same bound as the admission-log tuple, and an
out-of-bound epoch leaves that receipt pending instead of aborting later
collection. A matching admission job whose id is not a positive integer is
an incomplete lookup, not a missing job. The jobs request uses `per_page=100`
and follows `Link` rel=next only while the next URL stays on the allowlisted
API origin and that same run's jobs path, and only through the existing
ten-page cap. A
loop, a disallowed next URL, a `total_count` that does not match the jobs
already read, a full page with no next link and no `total_count`, or a page
past the cap is an incomplete listing, not proof that the admission job is
absent. Repeated identical complete blocks are one
identity. An incomplete block that only repeats those values is ignored. A
different value in a partial block or a second complete block is no identity.
A log larger than 64KiB is no identity, including when the first 64KiB already
contain a complete tuple and a conflicting tuple follows. The head becomes
`clawsweeper_failed` with no content verdict. That is not review PASS. While
the head remains `clawsweeper_queued` or `clawsweeper_running`, rereview
waits. After `clawsweeper_failed`, maintainer rereview is the supported
same-head recovery. An unproven failure stays a repository alert and does not
select a pull request. Creation time, workflow ref SHA, and "the only queued
pull" are not identities.

The admission job log is downloaded through the existing artifact redirect
transport. GitHub's job-log endpoint returns one 302, and only a
`productionresultssa*.blob.core.windows.net` target is followed. Private
runner log hosts, including `results-receiver.actions.githubusercontent.com`
and `*.actions.githubusercontent.com` pipeline hosts, stay outside that
allowlist until a separate approval. A refused redirect is no identity.

Dispatch sends `return_run_details: true`. GitHub's 2026-02-19 workflow
dispatch changelog returns HTTP 200 when that flag is set and HTTP 204 when
it is omitted. The current REST schema names the 200 body key
`workflow_run_id` (with `run_url` and `html_url`). The receipt stores that
integer key. A 204 response, or a 200 body without `workflow_run_id`, still
counts as dispatch and leaves the run id unset.

## Live activation is separate

Source profiles now list `issue_comment` beside `pull_request` and
`workflow_run`. This PR does not change the live GitHub App subscription,
permissions, webhook, or producer docs. Live command delivery stays blocked
until a separately authorized activation adds the `issue_comment` subscription.
No extra GitHub permission is required beyond the existing pull-requests scope.

## Cross-repo producer guidance

Conductor owns this command path. Do not broaden producer or target-repo docs
from this slice.

Observed dependencies that still promise a different entry:

- `saari-co/openclaw-smcbd-suite` PR #38 review text advised adding packaging
  proof to the PR body and expecting automatic rereview, or asking a maintainer
  to comment a re-review command. Body-edit auto-rereview is not implemented.
- ClawSweeper's own `@clawsweeper re-review` / `@clawsweeper review` docs describe
  a producer-native path. That is not Conductor admission and must not be treated
  as a bypass.

Those producer/target corrections need their own explicit owner slices.
