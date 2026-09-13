# OpenClaw exact-tuple contract adapter flags — 2026-09-13

## Identity and authority

- Repository: `saari-co/review-conductor`.
- Worktree: this isolated branch only.
- Branch: `openclaw/review-conductor-openclaw-exact-contract-flags-v1`.
- Exact base: `2f5818cacdf84a72b22362f984d0c3d8c18af244` (`origin/main`, merged PR #6).
- Exact head: the single local candidate commit that contains this packet.
- New source-scope repair count: `1/2`.
- Captured: `2026-09-13` America/New_York.
- Mutation authority: local source, tests, and one candidate commit.
- Not authorized: push, PR, review request, deployment, service/tunnel,
  credentials, webhook/App/protection/check-writer changes, live queue or
  model run, or source changes outside this worktree.

## Qualified adapter dependency

`tools/review_conductor.py` `command_preview` now fails closed unless the
`openclaw.enqueue` payload `review_epoch` is a non-negative `int` (bool
rejected) that equals the persisted `action['review_epoch']`. Only then does it
emit the queue command. Materialize bytes and every non-OpenClaw action are
unchanged.

Every queue command includes exactly once, in this order, the source-owned
selector and the bound epoch:

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

File SHA-256 values are in `candidate-manifest.json`.
`tools/review_conductor_userland.py` is now
`14e08a08187ef27adc95505051ff0334aaedcf8f17b3ee60490af25bd0f026ce`.

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
