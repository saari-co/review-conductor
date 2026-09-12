# Inactive service-core split proof — 2026-09-12

## Identity and authority

- Repository: `saari-co/review-conductor`.
- Base: protected `main` at `320534450bbfe924bf855b762a0f9358f0b40336`.
- Existing draft PR: #3, branch
  `openclaw/review-conductor-pr3-service-webhook`.
- Pre-split full candidate: `3b7516c892679a5ec302e84c3ee975c51331766d`.
- Reduced source-tree commit: `d3a026bf55d0987ff40cb9a0e5ffc30b11966bad`.
- A local preservation branch,
  `openclaw/review-conductor-pr4-live-adapters-full`, retains the pre-split
  candidate for the separately reviewed stacked adapter slice.
- Authorization covers reducing PR #3, pushing its existing branch, and
  creating a draft stacked PR for the deferred live adapters. It does not
  cover activation, credentials, App settings, webhook/tunnel changes,
  deployment, branch-protection changes, adjudication, or merge.

## Retained in PR #3

- GitHub webhook HMAC authentication before payload, registry, or policy work.
- Exact App, installation, repository, reviewer, policy, and review-tuple
  admission.
- Approved-policy bytes staged outside the engine write transaction and bound
  atomically with accepted delivery state.
- Durable exact-tuple binding and replay behavior.
- A descriptor-validated service-owned enrollment-registry loader.
- An inactive SMCBD candidate profile with verified non-secret App and
  installation identifiers and explicit activation blockers.
- An injected-ingestor seam that leaves the legacy default webhook path
  unchanged.

## Deferred to the stacked adapter PR

- GitHub installation-token minting and caching.
- Check publication and policy retrieval over GitHub transport.
- Checkout hydration.
- OpenClaw and ClawSweeper dispatch.
- Notification delivery.
- Per-side-effect authority fencing and read-only live binding verification.
- Credential readers, service worker/listener startup, HTTPS/tunnel setup, and
  deployment handoff.

The retained service entrypoint is a registry loader only. It has no executable
entrypoint, credential reader, worker, network listener, or external side
effect. The shipped SMCBD candidate remains disabled.

## Direct proof

- `make check`: passed.
- Focused service-core suite: 11 tests passed.
- Four precise service-core mutants were killed: authentication bypass,
  installation identity, approved-policy hash, and profile-policy agreement.
- `make build`: passed.
- `python3 -m compileall -q tools tests scripts`: passed.
- `git diff --check`: passed.
- Extraction provenance: 14 files verified and Python compilation passed.
- Net-diff audit against the base confirms no change to
  `tools/review_conductor_userland.py`; the only retained runtime adaptation is
  the optional injected-ingestor seam.

Hosted CI and the final exact branch head are reported after this evidence-only
follow-up commit is pushed. Local PASS is not deployment or activation proof.
