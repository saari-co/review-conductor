# Native execution failure projection — bounded source proof

## Assignment and identity

- Lane/type: bounded check-display and no-bundle execution-failure source repair.
- Owner: `conductor11_round1_and_labels_repair`; closer/mutation handoff: root.
- Repository: `saari-co/review-conductor`.
- Branch: `codex/native-failure-projection-20260915`.
- Isolated worktree: `review-conductor-codex-openclaw-original-report-20260915-codex-native-failure-projection-20260915`.
- Original base: `84966d938f7b63f044c86ff07a510ac102aa754c`.
- Fresh fetched and merged main: `bf44f69c42d75a74c57ab404361b403660d0239b`
  (accepted PR #12 handoff and evidence-bound recovery preserved).
- Status: bounded round-one repair; source owner holds this branch only.
- Allowed mode: source mutate only. Parent owns runtime and GitHub state.
- Stop: tested commit/non-force push/same-PR update and exact-head CI receipt. No
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
remain intact. The round-one repair below adds a narrow migration for verified owned terminal
skips; it never resets a terminal conclusion. Unconfirmed nonterminal updates
still fail closed.

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

## Round-one findings and label-response repair

Starting head: `5990dcba260a395bd0cb45b91c7d88882da7f944`; starting tree:
`44497ef569a57f3364082e99ab29bb8b419c721b`. PR #12 remains merged and unchanged.
Review `5214034299`, inline `4018774723`, and the two suppressed findings are
handled in this single repair batch. This lane requested no review.

| Finding | Disposition | Bounded correction |
| --- | --- | --- |
| Delayed `clawsweeper.started` after unbound terminal failure | `required_fix` — repaired | After validating the dispatched current tuple/epoch, reject starts referring to retired `terminal_attention_required` or `verdict_ingested` receipts before changing the head. An unrelated run is not fenced. |
| Existing completed/skipped checks cannot become nonterminal | `required_fix` — repaired | Read and validate the exact stored check ID, name, head, App and tuple-derived external ID; replace only a terminal `skipped` conclusion (including a retained skip after partial PATCH) through the existing durable create claim. Preserve its old ID/conclusion in an exact-tuple audit event. Never reset the old conclusion or replace a failure/success verdict. |
| Missing PATCH conclusion was treated like explicit null | `required_fix` — repaired | Queued/running readback requires the conclusion key to exist with JSON null, as well as the exact requested status. |
| Successful label POST/DELETE arrays rejected as objects | `required_fix` — repaired | Dedicated label-mutation array validation, with named-object entries; all other object endpoints remain strict. DELETE 404/204 stays idempotent, while malformed HTTP 200 responses and unexpected statuses fail closed. |

Late-start fencing is not attribution or terminal recovery: the unbound head
remains queued, the repository-level failure and alert remain, and explicit
operator recovery is still required. No per-PR failure verdict, report, finding
count, bridge artifact, or qualified terminal is manufactured.

Migration is part of normal authorized projection, not a new API writer or
review epoch. The old check retains its skipped conclusion; only one replacement is
active in the projection row. An uncertain POST remains `creating` and cannot be
retried automatically. Revocation before POST returns the existing claim to
`pending`. A confirmed replacement ID is committed before later label/comment
operations, so their failure cannot lose its identity or cause another POST.
The same authority guard still executes immediately before every mutation.

The label shapes match GitHub's documented [add-label response](https://docs.github.com/en/rest/issues/labels#add-labels-to-an-issue)
and [single-label removal response](https://docs.github.com/en/rest/issues/labels#remove-a-label-from-an-issue).
This source repair uses synthetic HTTP only; the parent-reported live failure
was not replayed here. No credentials, runtime database, live API mutation,
review request, merge or deployment was used to obtain proof.

### Regression evidence

`tests/test_openclaw_report_publication.py` now includes eleven bounded production
client regressions. Its HTTP transport returns real array-shaped label results;
production `_call`, endpoint allowlist, authority guard, check and publication
methods are not stubbed. The complete synthetic chain accepts native evidence,
projects ready-for-human, adds the ready label, publishes one issuer-owned
summary, commits projection state, and repeats without duplicate checks/comments.
Unowned labels survive. Wrong check identities, terminal verdict replacement,
missing/null readback, malformed responses, disallowed labels/statuses, authority
revocation, and uncertain replacement are exercised. A migrated check progresses
queued → in-progress → completed success under the same tuple/epoch, retaining
the old skipped check as historical evidence.

The four primary regressions were also executed against an isolated archive of
the exact starting head, with only the new test file overlaid: late start and
missing conclusion each failed by assertion; terminal-skip migration and the
ready-label/summary chain each errored on the old client behavior. All pass on
the repaired source. No operator checkout or branch was switched.

Final frozen-source `make check`: PASS, including 31 original-report/production
projection tests, the preserved #12 profile regressions, all legacy integration
checks, service and supervisor suites, provenance and whitespace. `make build`:
PASS. Source hashes below are verified against the tracked bytes. Hosted CI must
be read back for the pushed commit and attached to PR #11 by this lane before
handoff; prior-head CI is not reused.

## Source hashes

- `tools/review_conductor.py`: `16f3f7754805f81be40752859def84162c4aa059d9dabe7a5017e0ce64d03be5`
- `tools/review_conductor_runtime.py`: `d02a36a5ea09f959764c7ff9fb211694194f8098d12a457fc739054175cf472a`
- `tools/review_conductor_userland.py`: `37e5a2769089308c770a9488bde65978339093f1778741eed175655c9bee417b`
- `tests/test_review_conductor_userland.py`: `7db248f2223192caf6653583b6bc93c28fb63e0d6f1b3d0df1a6c4290ba2d4df`
- `tests/test_review_conductor_profiles.py`: `49efc0fb646f11e549263cc954ced0399592cab32597039968dda4372ec385d4`
- `tests/test_openclaw_report_publication.py`: `d0dbc5a5731e74499181f908ad9ecdaf3af680df1b86df8c90a0f89139d339ef`
- `tests/test_review_conductor_activation.py`: `da34d2378b2fefe25aa24ba026d60a8a64caadf6c89b9836e8fcca0c3931346b`
- `tests/test_review_result_projection.py`: `637bc74ce97efbf0b3e1cb97ccd53f31903e49f9ae8279aba7fa6e639174af48`
