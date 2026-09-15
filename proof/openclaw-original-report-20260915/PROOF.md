# Original OpenClaw report publication — source proof

## Route and ownership

- Lane/type: bounded report-publication source repair.
- Initial owner: `suite_producer_pin`; round-one repair owner: `conductor10_round1_repairs`; coordinator/closer: root.
- Repository: `saari-co/review-conductor`.
- Branch: `codex/openclaw-original-report-20260915`.
- Worktree: isolated sibling `review-conductor-codex-openclaw-original-report-20260915`, prepared with `smoky worktree new` after fetching origin.
- Base: `b54b47db85da197752e63f5c1896d56192d37dad` (fresh `origin/main`).
- Mode: source mutation; sole source owner. Runtime/merge/protection owners remain outside this lane.
- Stop: committed, pushed draft PR with local validation and current hosted-CI handoff. No review requests, ready conversion, merge, activation or historical-result edits.

## Scope and evidence

The existing collector preserved a status receipt but omitted the native
`review_output.txt` already fetched by the supported transport. This patch
preserves a separate bounded/digest-bound native report, validates it through
the existing terminal/event boundary, and shows its original text in the
existing owned check. Check details use GitHub's returned own-check URL only
after exact repository/check ID/name/head/App/external-ID validation.

No new hosting or artifact uploader is required. Run/artifact report-URL
validation, native ClawSweeper links, exact-tuple review authority and human-only
merge remain unchanged. Missing/oversized/invalid text is explicitly unavailable;
legacy terminals are not backfilled from local files.

## Offline checks

`tests/test_openclaw_report_publication.py` uses the real collector, terminal
bridge, persisted events, projection, and production check client with only
HTTP replaced by a recording transport. All input documents are synthetic.

- Original native bytes/digest and reviewer caveat survive to check output.
- Exact owned check URL replaces the generic integrator homepage on update.
- Repeated projection updates the same check IDs, without duplicate creation.
- Wrong epoch/request/base/head/report digest/path is rejected before acceptance.
- Report changes after acceptance are rejected before check writes.
- Wrong App/check ID/name/head/external ID/repository/URL/query/fragment is
  rejected before PATCH.
- Missing/oversized/invalid output has no false full-report claim.
- Literal report rendering remains bounded even with pathological fences.
- External run/artifact URL allowlist is unchanged; ClawSweeper links still
  address its real workflow run.
- Symlinked input is rejected; legacy omission does not trigger backfill.

Commands: `python3 tests/test_openclaw_report_publication.py`, `make check`,
`make build`, `git diff --check`. Final results and source hashes follow below.

## Limits and promotion gates

This is source/offline proof, not deployed publication or GitHub UI proof.
No live report, runtime record, enrollment, credential, or customer data is
included. The existing uncertain-create fence is preserved: if a check is first
created after terminal acceptance, the ID is persisted before its next normal
reconciliation reads and binds the actual GitHub page.

Exact-head CI and required source review must qualify the draft before its
separately authorized merge/deployment. Applicable owner acknowledgement gates
remain unchanged. A fresh prospective live report is required after deployment;
this source does not rewrite the failed historical publication attempt.

## Results

- Nine new publication regressions: PASS.
- `make check`: PASS (full suite, including Blocks compatibility and provenance).
- `make build`: PASS (local zipapp; not deployment).
- `git diff --check`: PASS.

## Tested source hashes

| Source | SHA-256 |
| --- | --- |
| `tools/review_conductor.py` | `d99d326e2d6856fe7b4100116e54753e76a12e28d423e387abc25128a79597c6` |
| `tools/review_conductor_runtime.py` | `ca4862bc8dfe427454997ba5e1b5a3911a6960910f509817a3a36b7341c98e70` |
| `tools/review_conductor_userland.py` | `b4b23601230c194ac8d75ad3090a01a86dd9b89c54f3b23b5923ad203e17bb44` |
| `tools/review_result_projection.py` | `8c245a952023d9f8f2a7622e7c08bc0e1ef8129e05bef8e83bf4e7d491065d59` |
| `tests/test_openclaw_report_publication.py` | `9a5525ee0e66b883038bfb0576da656d4bee2405a7a70dc70477922dac3528b7` |

## Copilot round 1 bounded repairs

- Reviewed source: `fd240baea5ed25a9185f5b64cdeb09ad1c8e52c5`.
- [Review 5210652509](https://github.com/saari-co/review-conductor/pull/10#pullrequestreview-5210652509): 3 inline findings, no suppressed findings.
- Budget: 1 of 2 Copilot rounds used. Root owns the only remaining request;
  this repair lane did not request review, merge or deploy.

| Finding | Disposition | Repair |
| --- | --- | --- |
| [Report lstat/open race](https://github.com/saari-co/review-conductor/pull/10#discussion_r4016171678) | required_fix | Single no-follow open, descriptor fstat and bounded read; disappearance before open emits missing; path replacement after open cannot redirect bytes. |
| [Unbounded fetch path](https://github.com/saari-co/review-conductor/pull/10#discussion_r4016171719) | required_fix | Require actual transport layout below configured source root; walk descendants with no-follow directory descriptors; validate exact request/action status before copying proof/report from the same directory descriptor. |
| [Empty text error](https://github.com/saari-co/review-conductor/pull/10#discussion_r4016171769) | required_fix | Distinct non-empty-text and oversized-text validation errors. |

### Source fidelity and boundaries

Read the existing x-api adapter `lanes/spark-openclaw-autoreview/run.sh`
and `lanes/lib/lane-receipt.sh`, alongside the tracked upstream fixture.
The adapter produces `runs/spark-openclaw-autoreview-runs/<generated-run-id>/PROOF.md`,
not a request-ID directory. Generated IDs have a UTC timestamp, PID and optional
collision suffix. Request identity remains in `REQUEST_STATUS.json` and is
qualified against the existing action. Both supported source-relative receipts
and absolute receipts inside the configured root work; unrelated paths do not.
Only the configured source root is trusted/canonicalized, not receipt-selected
subdirectories. No reviewed repository gets authority over the service root.

Status and proof leaves now use the same descriptor-bound regular-file reader
as the report so those files cannot reintroduce an ancestor/leaf traversal race.
Status/proof stay bounded to 1 MiB; report stays bounded to 24 KiB. Existing
ClawSweeper proof handling, exact-tuple qualification and merge authority remain
unchanged. This does not claim protection against a trusted same-user process
modifying an already-open regular file's contents in place.

Synthetic fixtures now follow the actual fetch layout and canonical configured
root. The initial full run revealed the disposable mutation-test subprocess's
`/tmp` alias mismatch; fixture canonicalization corrected it without weakening
any qualification or mutation assertions. The 25 profile tests, including
all precise applied-P3 mutants, then passed.

### Repair validation

- `python3 tests/test_openclaw_report_publication.py`: 16/16 PASS, including
  deterministic leaf replacement, disappearance, ancestor-directory replacement,
  symlinked status/proof/ancestor rejection, outside/traversal path rejection,
  wrong request rejection, actual relative transport receipt, nonregular/FIFO rejection and distinct errors.
- `python3 tests/test_review_conductor_profiles.py`: 25/25 PASS.
- Final `make check`, `make build`, `git diff --check`: results below.
- Limits: synthetic/offline source proof only; not deployed or live GitHub proof.
  Prior source review does not qualify the changed head; root retains remaining
  review/adjudication and owner gates.

### Final repair results and source identity

- `make check`: PASS (full suite, Blocks compatibility, profile mutation checks,
  extraction provenance and whitespace; exit 0).
- `make build`: PASS (exit 0).
- Final descriptor-cleanup/nonregular-file refinement: focused publication
  regressions rerun, 16/16 PASS; provenance/compilation and build rerun PASS.
- `git diff --check`: PASS.
- Commit starts at reviewed head `fd240baea5ed25a9185f5b64cdeb09ad1c8e52c5`
  on the same isolated branch. Exact resulting commit/tree and hosted CI are
  recorded in the PR body; hashes below bind the final tested code directly.

| Final repaired source | SHA-256 |
| --- | --- |
| `tools/review_conductor_userland.py` | `e34957e4bddfe818509ecba2658cc55aed992329f617f017a48b3ae8dbdf70b7` |
| `tools/review_result_projection.py` | `f17dd818ea5b111f5d8e4e626dadcc4f8dd832290eee8e8becdf7c2484d4f587` |
| `tests/test_openclaw_report_publication.py` | `3f01b90ccf2612ada575e891897515b3399131aa8057e2b1a17c06b9cdf355a9` |
| `tests/test_review_conductor_userland.py` | `d1c093a2f633ee18c918b10e55364f4301cef91f036c9b7eec37accd2153a196` |
