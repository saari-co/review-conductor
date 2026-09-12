# Authority-bound live-adapter proof — 2026-09-12

## Identity and scope

- Repository: `saari-co/review-conductor`.
- Stacked base: reduced inactive-core PR #3 at
  `c58921ee791eed9fdaab73b4bbf08d23db2e2247`.
- Pre-split full candidate: `3b7516c892679a5ec302e84c3ee975c51331766d`.
- Restored adapter commit: `9ca6f516cb8e0da4a57b7a814656a56f64df88ad`.
- Boundary repair commit: `d18d336664fa993a4414851ab48d624be44f8b5e`.
- Branch: `openclaw/review-conductor-pr4-live-adapters`.

This slice restores the live GitHub, checkout, OpenClaw, ClawSweeper,
notification, worker, credential, and operator-handoff code removed from PR #3.
It remains disabled and is reviewed independently from the admission core.

## Final authority-boundary repairs

1. Installation-token cache reads and replacement are synchronized, so a
   shared ingress/worker client issues only one concurrent mint.
2. Installation-token minting and read-only approved-policy retrieval are not
   fenced by an old tuple binding. This prevents a stale binding from blocking
   retrieval of the newly promoted policy. The subsequent repository mutation
   is still fenced after token minting.
3. The core profile's repository name and numeric ID must equal the
   service-owned enrollment on every provider read, delivery, and tick.
4. The authority-gate SQLite connection uses URI `mode=ro`; disappearance
   between the existence check and open is a failed gate, not empty state.
5. Checkout hydration is fenced before the checkout is touched and again
   immediately before a fetch.
6. The materialize and queue commands of an OpenClaw dispatch each receive a
   fresh authority fence.
7. Every individual notification send receives a fresh authority fence.
8. A live client must implement both guard installation and authority
   assertion; partial implementations fail closed.
9. Databases created before profile-digest binding are migrated in place, but
   their legacy rows remain untrusted until exact-tuple readmission.
10. Authority denial before transport releases claimed check, ClawSweeper,
    checkout and OpenClaw work back to `pending`; it is not misclassified as a
    failed or uncertain external side effect.

## Executable proof

- Focused live service/adapter suite: 59 tests passed.
- Executable mutation suite: all 57 precise mutants killed.
- The added cases directly exercise concurrent token minting, promotion after
  a stale binding, reloaded core identity, read-only/disappearing SQLite state,
  checkout fencing, both OpenClaw command fences, per-notification fencing, and
  incomplete live clients.
- Complete `make check`: passed before this evidence-only commit.
- Extraction provenance: 14 files verified and Python compilation passed.
- `git diff --check`: passed.

The complete gate, reproducible build, exact stacked-base/head whitespace check,
and hosted matrix are rerun on the final evidence commit. Local PASS is not
activation, deployment, or review clearance.

## Explicitly not done

- No App setting, key, secret, webhook, tunnel, credential, deployment, target
  repository, branch protection, check binding, merge, or adjudication change.
- The SMCBD candidate profile remains disabled.
- The proposed HTTPS endpoint remains unprovisioned and unverified.
- Fixture and hosted CI PASS are not genuine App-owned OpenClaw or ClawSweeper
  review PASS.
