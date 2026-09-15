# Native execution failure projection — bounded source proof

## Assignment and identity

- Lane/type: bounded check-display and no-bundle execution-failure source repair.
- Owner: `conductor_native_failure_projection`; closer/mutation handoff: root.
- Repository: `saari-co/review-conductor`.
- Branch: `codex/native-failure-projection-20260915`.
- Isolated worktree: `review-conductor-codex-openclaw-original-report-20260915-codex-native-failure-projection-20260915`.
- Fresh fetched base: `84966d938f7b63f044c86ff07a510ac102aa754c`.
- Allowed mode: source mutate only. Parent owns runtime and GitHub state.
- Stop: tested commit/non-force push/draft PR and exact-head CI receipt. No
  review request, merge, deployment, credential access or protection changes.

## Failure and bounded correction

A failed native workflow may finish before any qualified verdict bundle exists.
The collector already records its failure and alerts, but previously its check
projection could still say queued. A workflow-dispatch event identifies the
workflow commit, not necessarily the PR being reviewed. The dispatch transport
receipt also has no run ID. Timing or a single pending PR does not prove identity.

1. If a trusted `clawsweeper.started` event already bound the run ID to one current
   dispatched tuple and epoch, collection records `clawsweeper.execution_failed`
   and moves that head to `clawsweeper_failed`. This is an execution failure, not
   a review verdict. The run retains null verdict/proof fields. No content report,
   finding count, qualification or bridge artifact is manufactured.
2. Without that binding, collection still must not guess a PR. Reconciliation
   shows unresolved dispatched/unbound requests as `action_required`, stage
   `collection_attention_required`, explicitly stating that the failed repository
   run **is not bound to this PR**. The persisted head/action/verdict are unchanged.
   This is conservative repository-level collection attention, not a per-PR
   terminal claim. Pending-undispatched actions and bound/running/terminal heads
   do not receive this overlay. It does not retry or adjudicate anything.
3. Draft prerequisite waiting stays queued, not completed/skipped. A normal
   draft-to-ready transition on the same tuple/epoch therefore does not require
   resurrecting a terminal check. HTTP PATCH success alone is not confirmation:
   nonterminal updates must return the intended status with no retained conclusion.
   An unconfirmed update raises recovery-required instead of claiming success.

The existing owned check IDs, exact-tuple mutation authority, issuer gates,
uncertain-create fences, accepted-artifact publication and human merge policy
remain intact. An existing skipped check can be updated terminally to explicit
collection attention; no second check or invented run is created.

## Evidence and tests

Synthetic tests exercise real collector/state database, reconciliation and
production check create/update methods with only GitHub transport replaced:

- Unbound failure remains unbound but no longer projects as queued.
- Existing skipped check updates to action_required using the same check ID;
  no accepted artifact/content verdict is rendered.
- Exact started binding becomes failed without a verdict bundle.
- Wrong run ID and stale epoch cannot terminalize the current request.
- Repeated collection is idempotent; no reviewer artifacts are synthesized.
- Undispatched requests are not held by an unrelated failed workflow.
- Draft-ready retains one epoch and a nonterminal prerequisite check.
- Retained terminal conclusions reject nonterminal PATCH confirmation; normal
  queued-to-running and terminal attention updates still work.

The retained-conclusion test models the observed partial PATCH result; it is
not a live GitHub API experiment. GitHub documents optional update fields and
that specifying a conclusion completes the check; it does not document a null
conclusion reset. See [GitHub Checks API](https://docs.github.com/en/rest/checks/runs#update-a-check-run).
No undocumented reset or fake passing conclusion is used.

## Results and remaining handoff

Local `make check`, `make build`, and `git diff --check`: PASS. Hosted
exact-head CI is linked in the draft PR and terminal handoff. No external review
clearance is claimed by these deterministic tests.

The root still owns actual failed-run diagnosis, qualified source deployment,
explicit runtime recovery, and a genuine successful native review. This change
cannot create the missing verdict. Unbound collection attention stays explicit
until the actual request reaches a qualified terminal state or root performs
supported recovery; the source does not silently discard an unresolved run.

## Source hashes

- `tools/review_conductor.py`: `b6bcfbeb124cb577acb0805109f99a99b741bee5882c9bcbae7da3289730807e`
- `tools/review_conductor_runtime.py`: `af2452ed1d760cd5d99afd574b9825b9148df9e8b13c9b9720d8dae3cd310715`
- `tools/review_conductor_userland.py`: `37e5a2769089308c770a9488bde65978339093f1778741eed175655c9bee417b`
- `tests/test_review_conductor_userland.py`: `8e84c5096f1b450650b33c0b8784ac762d03c2eb8df03745f7b626821c197230`
- `tests/test_review_conductor_profiles.py`: `cb10897cc83936a5c28726c742d68311fb2b981c1e127e6ce8b335097d70ea50`
- `tests/test_openclaw_report_publication.py`: `1f3aad1793903a857511c5132e05930d8c4222980bb94722cf958c4d2373ab4c`
