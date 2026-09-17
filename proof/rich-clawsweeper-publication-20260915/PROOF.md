# Rich ClawSweeper publication — source proof

Date: 2026-09-15. Lane/owner: `rich_publication_implementation`; closer: root.
Round 1 repair owner: `conductor15_round1_repairs`; root retains review/closeout.
Final repair owner: `conductor15_final_proof_status_fix`; root retains adjudication.
Task: restore native presentation through Conductor's existing publication owner.
Mode: source/tests/docs/proof and draft PR only. No runtime activation or review verdict.

**Merge hold: Suite #4 → #6 → #5 → #7 must land before this restoration.**
Refresh this draft against that landed stack and rerun qualification before merge.
No ready transition, reviewer dispatch, merge, deployment, credentials or protection
changes were performed or authorized by this proof.

## Exact source and provenance

- Repository: `saari-co/review-conductor`.
- Branch: `codex/restore-rich-clawsweeper-publication-20260915`, isolated sibling worktree.
- Fresh base: `8cefc3548d40b3868263cacd54a9568d51fbe67f`.
- Round 1 repair baseline: `b99e1d4289a53e2b1f48dea7b0a9224c78e07078`.
- Final repair baseline: `75e5753634ab3e6d0e71ad8dd18ff397d1a9c3fe`.
- Producer contract: `saari-co/clawsweeper@f1d79d1234897abca168807faf8a2088525a0e98`.
- [Source hashes](source-hashes.json) bind the tested implementation/fixture files.
- [Provenance ledger](../../docs/provenance.json) records the extracted-file
  adaptations and pinned producer source blob hashes.
- Producer anchors: `src/clawsweeper-policy.ts` (native label vocabulary and
  section names), `src/clawsweeper-label-selection.ts` (proof/priority/risk
  selectors), `src/clawsweeper-rating.ts` (overall rating selector),
  `src/clawsweeper-report-comment-presentation.ts` (public section layout),
  `src/clawsweeper-report-helpers.ts` (safe diagram boundary),
  `src/clawsweeper-types.ts` (evidence kinds),
  `src/clawsweeper-report-parser.ts` (native maintainer proof override origin),
  `src/clawsweeper-promotion-facts.ts` (producer override proof evidence).

There is no producer change, Node dependency, second publisher, new evidence
store or database schema. The existing terminal receipt now retains its already
validated report digest; an existing-event-log ownership receipt precedes native
publication mutations. Existing precedence, adjudication, four
Conductor status-label meanings and human-only merge are unchanged.
The final repair below corrects classification admission of producer-valid proof
statuses; it does not change their producer meaning or create owner authority.

## Reviewable synthetic artifacts

- [Native fixture](../../tests/fixtures/clawsweeper-rich-report.md): fabricated
  identity, findings/evidence narrative and flowchart, never a live review.
- [PREVIEW](PREVIEW.md): generated Markdown with native public sections,
  score/tier names, proof/evidence, supplied Mermaid, findings/next steps and
  exact digest/run reference. **Not verified GitHub rendered/live output.**
- [Expected labels](expected-labels.json): exact native names plus separate
  Conductor status, owned families and manual/foreign preservation examples.
- [Tests](../../tests/test_clawsweeper_presentation.py): complete synthetic
  accepted-bundle → stored report → bridge receipt → publication path.

Reproduce the generated comment without credentials:

```sh
python3 - <<'PY'
import sys
sys.path.insert(0, 'tests')
from test_clawsweeper_presentation import presentation, plan_for
identity, report = presentation()
print(plan_for(identity, report)['comment']['body'])
PY
```

## Positive and negative claims

| Case | Qualified observation |
| --- | --- |
| Accepted clean native report | What This Changes, original three-tier scale, verification/evidence, supplied flowchart, findings/next steps and digest/run are present. |
| Repeated tick / retry after label failure | One App-owned comment; no repeated native-label changes; pre-write ownership survives interrupted publication. |
| Native findings / deficient proof | Runtime waiting-on-author / needs-proof statuses remain authoritative; native score cannot clear findings. P3 triage is not rewritten to finding severity. |
| Native family transitions | Exact S/A/B/C/D/F/NA vocabulary, P0–P3/none, sufficient state, independent screenshot/video media, eight native risk labels; obsolete scoped labels removed. |
| Missing optional fields | No invented patch score, priority, media/risk ownership or diagram. |
| Report mutation | Missing, altered, oversized, symlinked, directory and FIFO reports fail before current publication writes. |
| Receipt / identity conflict | Accepted digest, actor, run, scope and full report tuple/epoch must match; malformed terminal digest fails event validation. |
| Foreign comment / manual labels | Wrong-App comment untouched; `proof: override`, `rating: manual`, custom risk and ordinary labels preserved outside explicitly activated families. |
| Closed / changed head | Journaled native labels retracted; old owned comment explicitly historical, not current clearance. |
| Untrusted presentation | HTML, control markers, fences and mentions neutralized; unknown/malformed label instructions rejected; unsafe/missing diagram not rendered. |
| Native declaration case | `flowchart lr`, `FLOWCHART TB` and `FlOwChArT bT` survive unchanged into the Mermaid fence; all seven existing unsafe suffixes remain rejected across uppercase, lowercase and mixed-case declarations (21 combinations). |
| Output bounds | Per-section excerpts explicitly marked; 512 KiB input bound and 48 KiB output budget; whole-rich-body fallback preserves original artifact access. |
| Label API availability | Existing scoped POST/DELETE only, response confirmation required, already-absent removal idempotent; rejected/unconfirmed addition is failure, no bootstrap or destructive replacement. |
| Legacy receipts / Blocks | No historical backfill from nearby files. Missing retained digest and legacy Blocks retain explicit compact-summary behavior. |

## Read-only actual-artifact compatibility

The previously accepted native report was read for compatibility only:
19,116 bytes; SHA-256
`96ecd2c21511f9a8c75fc8c6709e1957cb31ddd8381ba8d6b6c86982d0bc3d64`.
The parser accepted its exact reported identity and found 13 allowed public
sections. Its supplied flowchart passed the safe Mermaid subset; native metadata
produced `P3`, `proof: sufficient`, `rating: 🐚 platinum hermit` (B/B/B,
sufficient/live_output, no risk labels). The selected public rendering was 7,403
UTF-8 bytes. Only sanitized shape/results are recorded here; **no raw production
artifact, comment, runtime database or credential was copied into Git**.

This demonstrates parser/presentation compatibility, not a re-review, accepted
runtime backfill, deployed label parity or a live GitHub render. Old terminal
receipts lacking a persisted digest are intentionally unavailable for rich
publication until a separately accepted prospective report provides that binding.

## Copilot Round 1 repairs

The coordinator adjudicated both inline findings from
[review 5216994149](https://github.com/saari-co/review-conductor/pull/15#pullrequestreview-5216994149)
as `required_fix`:

- [4021204883](https://github.com/saari-co/review-conductor/pull/15#discussion_r4021204883):
  `safe_diagram` now matches only the declaration case-insensitively, following
  the pinned producer's `sanitizeArchitectureDiagram`. No unsafe-content filter,
  bounds, admission, label, grade or verdict logic changed.
- [4021204916](https://github.com/saari-co/review-conductor/pull/15#discussion_r4021204916):
  [architecture](../../docs/architecture.md) and the directly overlapping
  [integration contract](../../docs/integration-contract.md) explicitly supersede
  the old status-only/comment split. Reviewer services retain original evidence
  authority; Conductor owns the bounded comment and allowlisted native-label
  publication. Exact tuple/epoch/digest/admission, status semantics, independent
  adjudication, human-only merge and inactive qualification remain unchanged.

Before the renderer fix, `git archive` of the exact repair baseline was extracted
into a disposable temporary directory and only the updated test file was copied
there. Running the two tests below produced three expected assertion failures
for the valid declaration variants and a passing unsafe-content test. The
assigned checkout was never reset or switched. After the one-flag repair, both
tests and the complete 18-test focused suite passed.

```sh
python3 tests/test_clawsweeper_presentation.py \
  PresentationTests.test_native_lowercase_and_mixedcase_diagrams_are_preserved \
  PresentationTests.test_marker_html_and_fence_injection_and_unsafe_diagrams_are_not_active
```

All six pinned producer blob hashes and all ten implementation/test/contract
hashes in `source-hashes.json` were independently checked. The existing synthetic
preview body, excluding its explanatory preamble, is byte-identical to current
renderer output. Changed contract links resolve locally; the diff preserves
admission/evidence, no-regrading, adjudication and human-only merge boundaries.
Only the renderer/tests, sanitized proof/provenance and contract text changed. No raw report,
credential, runtime configuration or service state was added.

## Final Copilot repair — proof-status contract

The coordinator adjudicated the sole finding
[4021290511](https://github.com/saari-co/review-conductor/pull/15#discussion_r4021290511)
from [review 5217081204](https://github.com/saari-co/review-conductor/pull/15#pullrequestreview-5217081204)
as `required_fix`. **Copilot budget: 2/2 rounds closed. These final fixes are
TESTED, NOT RE-REVIEWED; no further review request is authorized or made.**

The pinned producer's `REAL_BEHAVIOR_PROOF_STATUSES` contains exactly
`sufficient`, `missing`, `mock_only`, `insufficient`, `not_applicable`, `override`.
Its rating code treats `mock_only` like deficient `insufficient`; native
`override` originates from recorded maintainer `proof: override` and is existing
proof evidence. Conductor now admits and renders this exact six-value vocabulary.
It rejects obsolete `not_needed` / `failed` / `required` and unknown values before
failure, findings, maintainer or contributor-action precedence could skip their
validation. This is a native compatibility correction, not an admission-policy
or producer change.

| Native evidence | Qualified behavior |
| --- | --- |
| `mock_only` | Materializes, renders its unchanged status, publishes `status: 📣 needs proof`, retracts stale `proof: sufficient`; never becomes clean or ready through its rating. |
| `override`, otherwise clean | Materializes and renders unchanged as native proof evidence, retains `owner_merge_authority` and `merge_authorized: false`; cannot mint `proof: override` or `proof: sufficient`. |
| `override` with findings / maintainer question / contributor action | Existing findings, human-policy, or proof-deficient status retains precedence; override cannot clear any of these gates. Execution-failure and non-ready-rating precedence are also regression checked. |
| Human-owned `proof: override` already present | Preserved outside native ownership; no POST or DELETE creates/removes that owner authority. |
| Notification qualification | `READY_PROOF_STATUSES = {"sufficient"}` remains byte-for-byte unchanged; both newly supported values store `ready_qualified = 0`. |
| Repeated reconciliation | One rich comment, unchanged final labels and no repeated native label additions. Existing ready-status/check reconciliation may repeat its idempotent calls; this repair does not change that separate behavior. |

The [tracked regression receipt](proof-status-regression.json) binds the final
test-file hash, exact archived baseline and reproduction command. On archived
`75e5753634ab3e6d0e71ad8dd18ff397d1a9c3fe`, with **only** the updated test file
copied into disposable extraction, five primary tests yielded 21 expected
assertion failures and two expected valid-status rejection errors. On repaired
source the same five tests pass. All 28 focused tests pass, including six-value
acceptance, 20 invalid-value/precedence combinations, synthetic accepted-bundle
collection → materialization → bridge → rich publication → repeat, preserved
actual findings/gates, human label preservation, and rejected findings-bearing
noncontract evidence before report/terminal materialization.

Only two production modules change in this final repair. Repository/PR/base/head/
epoch/digest/actor binding, action/admission authority, sole Conductor publication,
unowned-label protection, human-only merge, profile/registry and notification
policy are unchanged. No historical stored evidence is rewritten. Existing
compact-summary and Blocks fallback remain; newly parsed native bundles validate
the producer vocabulary. No live report, database, credential, runtime or Suite /
ClawSweeper source/PR was changed or used in the synthetic final-repair tests.

## Validation and gates

Focused synthetic suite: 28 tests passed. Existing projection (22), original
OpenClaw/publication (31), and profile/Blocks compatibility (31) suites passed
during implementation. Full required checks and build are recorded below after
terminal execution; CI URLs and exact PR head are bound in the draft PR and
external implementation receipt to avoid self-referential commit evidence.

`make check`: PASS (all required suites, staged source hygiene, extraction hashes, Python compilation and diff whitespace).

`make build`: PASS (`dist/review-conductor.pyz`, ignored build output).

`git diff --cached --check`: PASS after removing blank quote-line trailing whitespace in the generated preview.
The Round 1 renderer fix was covered by all 16 `make check` suites and the focused
18-test run. The 12 unittest suites reported 275 tests; the four existing custom
test programs also completed successfully, including userland (21) and
projection (22). No aggregate count is inferred for the other two custom programs.
No checks were skipped; no live credentials or runtime service were used.

The final proof-status repair also passed all 16 `make check` suites and
`make build`: the 12 unittest suites reported 285 tests (including the 28 focused
tests), and all four custom programs passed, including userland (21) and
projection (22). Final provenance/source/producer hashes, unchanged synthetic
preview, local documentation links, staged repository hygiene and whitespace
were checked. No checks were skipped; this final-repair qualification used only
mocked synthetic fixtures and disposable local state.

Exact-head CI and the published final repair SHA are recorded in the draft PR and
external final repair receipt after push; this tracked proof does not claim a
self-referential commit or external-review clearance. Copilot rounds are exhausted
at 2/2; no third round or replacement precommit-review gate is introduced.

Remaining gates: final fixes are TESTED, NOT RE-REVIEWED; coordinator adjudication,
prerequisite merge order, refreshed final qualification, required human owner
acknowledgements, explicit human merge and separate activation remain held.
PR 15 stays draft. No deployed parity or live GitHub-rendering claim is made.
