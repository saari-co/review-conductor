# Original OpenClaw report publication — source proof

## Route and ownership

- Lane/type: bounded report-publication source repair.
- Owner: `suite_producer_pin`; coordinator/closer: root.
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
