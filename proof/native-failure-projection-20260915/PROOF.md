# Native execution failure projection — bounded source proof

## Assignment and identity

- Lane/type: bounded check-display and no-bundle execution-failure source repair.
- Owner: `conductor11_remove_unbound_overlay`; closer/mutation handoff: root.
- Repository: `saari-co/review-conductor`.
- Branch: `codex/native-failure-projection-20260915`.
- Isolated worktree: `review-conductor-codex-openclaw-original-report-20260915-codex-native-failure-projection-20260915`.
- Original base: `84966d938f7b63f044c86ff07a510ac102aa754c`.
- Fresh fetched and merged main: `bf44f69c42d75a74c57ab404361b403660d0239b`
  (accepted PR #12 handoff and evidence-bound recovery preserved).
- Status: bounded parent-adjudicated repair; source owner holds this branch only.
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
2. Without that binding, collection must not guess a PR. The failed run and
   repository-level alert remain recorded, but **no per-PR overlay is applied**.
   The parent rejected the first candidate's `unbound_clawsweeper_attention`
   fallback: any historical failure could put every later unbound dispatched PR
   into action-required. The helper and both projection overrides are removed.
   Pending and dispatched requests retain their own state-derived queued checks;
   no finding, artifact or failed-run association is invented.
3. Draft prerequisite waiting stays queued, not completed/skipped. A normal
   draft-to-ready transition on the same tuple/epoch therefore does not require
   resurrecting a terminal check. HTTP PATCH success alone is not confirmation:
   nonterminal updates must return the intended status with no retained conclusion.
   An unconfirmed update raises recovery-required instead of claiming success.

The existing owned check IDs, exact-tuple mutation authority, issuer gates,
uncertain-create fences, accepted-artifact publication and human merge policy
remain intact. This repair does not revive existing terminal/skipped checks or
modify stored check conclusions; unconfirmed nonterminal updates still fail closed.

## Evidence and tests

Synthetic tests exercise real collector/state database, reconciliation and
production check create/update methods with only GitHub transport replaced:

- The negative regression fails against the old overlay and passes after removal.
- An existing request and a later fresh request both retain queued checks after
  an unrelated unbound failure, in pending and dispatched states. Real projection
  and injected-transport check output contain no unrelated run, verdict or artifact.
- The failed run stays unbound, with null verdict/proof, and alerts remain available.
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
cannot create the missing verdict or attribute an unbound workflow to a PR.
Repository-level failure records/alerts are retained. No dispatch correlation
mechanism, live recovery, reviewer invocation, merge or deployment is added.

## Source hashes

- `tools/review_conductor.py`: `4ac7aa2e911c97fdb7391f7ce46d8f16db8c783ce10208ed14fcad8348b8cfd7`
- `tools/review_conductor_runtime.py`: `769a800d5a9afb65063d853fabcf5c9d8e1ea69155c18b672763300af6d6c1c1`
- `tools/review_conductor_userland.py`: `37e5a2769089308c770a9488bde65978339093f1778741eed175655c9bee417b`
- `tests/test_review_conductor_userland.py`: `7db248f2223192caf6653583b6bc93c28fb63e0d6f1b3d0df1a6c4290ba2d4df`
- `tests/test_review_conductor_profiles.py`: `49efc0fb646f11e549263cc954ced0399592cab32597039968dda4372ec385d4`
- `tests/test_openclaw_report_publication.py`: `1f3aad1793903a857511c5132e05930d8c4222980bb94722cf958c4d2373ab4c`
