# OpenClaw exact-tuple contract adapter flags — 2026-09-13

## Identity and authority

- Repository: `saari-co/review-conductor`.
- Worktree: this isolated branch only.
- Branch: `openclaw/review-conductor-openclaw-exact-contract-flags-v1`.
- Exact base: `2f5818cacdf84a72b22362f984d0c3d8c18af244` (`origin/main`, merged PR #6).
- Historical first-packet exact-head wording claimed this packet contained its
  own commit SHA. That is impossible and is not current binding.
- Code-source SHA (cycle-2 repaired source; this F6 proof edit does not change
  runtime source): `7b1267f812ac0822c991f2cb7dc24a9634e302b1`.
- Historical first-packet source-scope repair count: `1/2` (snapshot at
  `proof_source_head` `88aed1d8824f480134728e8f442f9a9aac5f92e7`).
- Authoritative source-scope repair count at the code-source SHA: `2/2`.
- Historical model review rounds: `2/2` terminal, not clean. They are not
  current-head review clearance.
- Final published head/base live only in the closeout-owner external receipt;
  this packet does not contain its own commit SHA.
- Captured: `2026-09-13` America/New_York; F6 binding corrected `2026-09-14`.
- Historical first-packet mutation authority was local source, tests, and one
  candidate commit, with push/PR unauthorized. This F6 closeout lane may
  commit the tracked proof binding, non-force push the existing branch, and
  update PR #7 body only.
- Still unauthorized: review request, merge, deployment, service/tunnel,
  credentials, webhook/App/protection/check-writer changes, live queue or
  model run, activation, or source changes outside this worktree.

## Qualified adapter dependency

`tools/review_conductor.py` `command_preview` now fails closed unless the
`openclaw.enqueue` payload `review_epoch` is a non-negative `int` (bool
rejected) that equals the persisted `action['review_epoch']`. Only then does it
emit the queue command. Materialize bytes and every non-OpenClaw action are
unchanged.

Historical first-packet snapshot below described every OpenClaw queue command
as emitting these flags. Current source is capability-gated: only
`openclaw.exact_tuple_contract=review-conductor-openclaw-v1` emits them;
legacy profiles keep the legacy vector. The ordered flags remain:

Every qualified exact-tuple queue command includes exactly once, in this
order, the source-owned selector and the bound epoch:

```text
--exact-tuple-contract review-conductor-openclaw-v1
--review-epoch <persisted-action-review-epoch>
```

Epoch `0` is the first valid generation. The selector is a Conductor
source-owned constant, not target policy or caller config. Conductor does not
pass or stamp `review_scope`, `reviewer_actor`, or priority. Those remain
fixed by the unpublished x-api source-owned contract
(`review_scope=comprehensive`, `reviewer_actor=spark-openclaw`,
`native_max_priority=P3`). Generalized `review_policy` profiles in
`collect_openclaw_terminals` now also require Spark `REQUEST_STATUS.json`
`native_max_priority=P3`, `applied_max_priority=P3`, and
`exact_tuple_qualified is True` before any terminal artifact write. Missing,
P0, conflicting, or false fields fail closed for clean and findings results.
Copied request `comprehensive` while native execution remains P0 is rejected.
Legacy profiles without `review_policy` stay compatible. Review prose and
adapter exit code are not applied-P3 evidence.

Exact emitted queue vector:

```text
<smoky> lane run spark-openclaw-autoreview --queue
  --queue-request-id <id> --operator-id <operator> --mode branch
  --base <base> --remote-worktree <path> --pr-url <url>
  --exact-tuple-contract review-conductor-openclaw-v1
  --review-epoch <persisted-action-review-epoch>
```

## Direct tests and mutants

- Epoch `0` planned and applied command vectors match the exact ordered
  materialize-then-queue lists; both flags occur once; actor/scope/priority
  tokens are absent; retry/idempotency remains `already_dispatched`.
- Reopened epoch `1` planned queue vector uses `--review-epoch 1` from the
  persisted action identity.
- Payload epoch values `-1`, `true`, `"0"`, and a mismatched positive integer
  cannot produce commands or invoke the fake adapter; the action stays pending.
- Three disposable-copy queue mutants were killed by one named test each:
  1. omit `--exact-tuple-contract`;
  2. omit `--review-epoch`;
  3. source the epoch only from payload JSON.
- Generalized `collect_openclaw_terminals` consumers reject missing/P0/
  conflicting/`false` applied-P3 fields with zero artifact write for both
  clean and findings results; a copied `comprehensive` plus native P0 status
  is rejected. Legacy profiles without `review_policy` still materialize
  status that omits those fields.
- Three disposable-copy consumer mutants were killed by one named test:
  1. omit `native_max_priority=P3`;
  2. omit `applied_max_priority=P3`;
  3. omit `exact_tuple_qualified is True`.

## Verification

Locally exercised interpreter: CPython 3.14.6 on macOS.

- Focused vector and reject tests — PASS
- Focused mutation harness — PASS (3 queue mutants killed)
- Focused consumer applied-P3 tests — PASS
- Focused consumer mutation harness — PASS (3 killed)
- `python3 tests/test_review_conductor.py` — PASS
- `make check` — PASS, including generalized and legacy userland terminal
  validation
- `make build` — PASS
- `python3 -m compileall -q tools tests scripts` — PASS
- `scripts/check_provenance.py` — PASS; destination SHA and adaptation updated
  for the adapted extracted `tools/review_conductor.py`,
  `tools/review_conductor_userland.py`, and their direct tests
- `git diff --check` — PASS on the working tree
- Exact committed `2f5818cacdf84a72b22362f984d0c3d8c18af244..HEAD` whitespace
  is run after the local commit
- Workflow files were not changed; actionlint was not required

File SHA-256 values of the current code-source files are in
`candidate-manifest.json` and match `docs/provenance.json`.
Historical first-packet digest of `tools/review_conductor_userland.py` at
`88aed1d8824f480134728e8f442f9a9aac5f92e7` is
`14e08a08187ef27adc95505051ff0334aaedcf8f17b3ee60490af25bd0f026ce`.
Current code-source digest is
`c27d8c45e45ffa2b10c6f1680f425b7453775ef5af72d5e801d0048a937126a2`.

## Untouched and live boundaries

No live external calls. Synthetic adapters and temporary state only.

Untouched: x-api, spark-dgx, `openclaw-smcbd-suite` #8, the Saari ClawSweeper
producer, credentials, webhook/App/protection/check-writer surfaces, live
queue/model execution, remote Git, Gateway, Smoky runtime activation, target
repositories, merge, and adjudication.

## Remaining gates

This packet does not attach or pin the x-api exact-tuple transport, and it does
not provide or install a spark-dgx applied-P3 attestation source. Those remain
explicit later source/install gates. Naming the adapter command dependency is
not publication, installation, or activation.

## Repair — 2026-09-13 (Copilot review 5192995503)

Historical cycle-2 source repair on isolated branch
`openclaw/review-conductor-openclaw-exact-contract-flags-v1` from
`proof_source_head` `88aed1d8824f480134728e8f442f9a9aac5f92e7` to code-source
SHA `7b1267f812ac0822c991f2cb7dc24a9634e302b1`. A commit cannot contain its
own SHA. The earlier pending `final_head_receipt` placeholder is removed;
this packet now records `proof_source_head`, `code_source_sha`, and file
hashes, and points at the closeout-owner external final-head receipt.

### Per-finding disposition

| Finding | Disposition | Location |
| --- | --- | --- |
| 4001479555 | required_fix applied | `tools/review_conductor_userland.py:694` via `tools/review_conductor.py:163` `same_typed_value` |
| 4001479572 | required_fix applied | persist at `tools/review_conductor_userland.py:724-727`; enforce at `tools/review_conductor_runtime.py:1860-1878` and `tools/review_conductor.py:1481-1498` |
| 4001479530 | required_fix applied | `tools/review_conductor.py:1893` gated by `openclaw.exact_tuple_contract` |
| 4001479583 | required_fix applied; F6 replaces pending placeholder | `proof_source_head` + `code_source_sha` + file hashes; `final_head_receipt` is the external closeout-owner receipt contract, not a self-SHA |

### Binding scheme

`proof_source_head` is the historical pre-repair snapshot
`88aed1d8824f480134728e8f442f9a9aac5f92e7`. `code_source_sha` is the cycle-2
repaired source `7b1267f812ac0822c991f2cb7dc24a9634e302b1`. File hashes are
sha256 values of those code-source/test files and match `docs/provenance.json`.
`final_head_receipt` is the external closeout-owner final head/base receipt
and independent matrix contract. This packet must not contain its own commit
SHA.

### Companion admission evidence (finding 4001479530)

The tracked legacy Spark parser
`tests/fixtures/upstream-spark-openclaw-autoreview.txt` L34-57 exits `2` on
`--exact-tuple-contract` / `--review-epoch`. That fixture is the
incompatibility proof for un-gated emission. The companion parser that accepts
the new flags is the x-api lane in saari-co/x-api PR #670,
`lanes/spark-openclaw-autoreview/run.sh`. This repository does not contain or
run that companion lane. Legacy Blocks profiles omit
`openclaw.exact_tuple_contract` and keep the legacy argv. The inactive SMCBD
profile declares `exact_tuple_contract=review-conductor-openclaw-v1` and emits
the new flags.

### Commands and results

- `python3 tests/test_review_conductor.py test_legacy_openclaw_queue_omits_exact_tuple_flags test_openclaw_queue_command_binds_exact_tuple_contract test_openclaw_queue_rejects_unbound_payload_review_epoch` — PASS
- `python3 tests/test_review_conductor.py test_precise_openclaw_exact_contract_mutants` — PASS (3 killed)
- `python3 tests/test_review_conductor_profiles.py` focused collector/bridge/type-coercion/capability tests — PASS
- `python3 tests/test_review_conductor_profiles.py OpenClawTerminalMutationTests.test_precise_openclaw_applied_p3_mutants` — PASS (3 killed)
- `python3 tests/test_review_conductor_userland.py` — PASS (19), including legacy artifact bridge
- `make check` — PASS (governance, all test suites, provenance, `git diff --check`)
- `make build` — PASS (`dist/review-conductor.pyz` generated, not tracked)
- `git diff --check` — PASS on the working tree
- Interpreter: CPython 3.14.6 on macOS; no x-api, OpenClaw, Smoky, or live credentials

## F6 — honest proof bindings — 2026-09-14

Frozen closeout disposition F6 for this Conductor packet only. No runtime
exception or source-behavior change. F4 remains `reject_false_positive`:
`tools/review_conductor_runtime.py` `class RuntimeError(core.ContractError)`
is caught by the existing `core.ContractError` handler. Historical finding
4001479583 asked this packet to contain its own exact-head SHA; that is
impossible. The later pending-placeholder repair is also removed.

### Distinctions

| Claim | Status |
| --- | --- |
| Historical first-packet snapshot `88aed1d8824f480134728e8f442f9a9aac5f92e7` | `1/2` source-scope count; userland digest `14e08a08...`; not current source |
| Cycle-2 code-source SHA `7b1267f812ac0822c991f2cb7dc24a9634e302b1` | `2/2` source-scope count; current file digests; unchanged by this F6 edit |
| Historical model review rounds | `2/2` terminal, not clean; not clearance of this or any later head |
| Source-only companion compatibility | no-network matrix in Spark #179 against this code-source SHA and x-api `ca7bc76a6e396b536e861a74ee08857224148744`; not installation |
| Hosted checks | exact-head GitHub Actions CI only; not review or merge clearance |
| Current-head review applicability | prior rounds do not apply to a later head; no new review is requested or invented |
| Live installation | unproven; no enrollment, live queue/model review, check writer, or activation |

### External final receipt

`final_head_receipt` =
`external:BOUNDED-CLOSEOUT-DISPOSITIONS.md#F6-closeout-owner-final-head-base-receipt`.
The closeout owner, not this commit, records the exact final head/base and
the independent final-head matrix for Conductor, x-api, and spark-dgx. This
packet tracks code-source SHA plus file hashes only.
