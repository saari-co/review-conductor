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

A `terminal_pending_verdict_bridge` artifact that does not match exactly one
current dispatched action is retired as historical attention. Superseded,
closed, and unbound bundles take this path. Retirement writes no verdict, does
not change any head, and does not stop later runs or projection in the same tick.

A non-success run with no exact verdict bundle fails the current head only when
that run is the already-started request, the dispatch receipt stores that
workflow run id, or the single admission job log shows one consistent
`pr_number`, `expected_base_sha`, `expected_head_sha`, and `review_epoch` for
the current dispatched tuple. The head becomes `clawsweeper_failed` with no
content verdict. That is not review PASS. While the head remains
`clawsweeper_queued` or `clawsweeper_running`, rereview waits. After
`clawsweeper_failed`, maintainer rereview is the supported same-head recovery.
An unproven failure stays a repository alert and does not select a pull request.
Creation time, workflow ref SHA, and "the only queued pull" are not identities.

Dispatch asks GitHub for `return_run_details` and stores `workflow_run_id` on
the receipt when the host returns it. A 204 response still counts as dispatch
and leaves the run id unset.

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
