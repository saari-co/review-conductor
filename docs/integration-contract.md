# Repository manifest and service boundary v1

## Repository-owned requirements

A target may commit `.review-conductor.json` using
[`target-manifest.schema.json`](../contracts/target-manifest.schema.json).
The examples are proposals only; no target repository has been changed/enrolled.

The manifest contains only repository name, default branch, CI workflow name/path,
quiet period, comprehensive scope, ordered required rails, and human-only merge.
V1 requires at least 600 seconds quiet time and both comprehensive rails. The
unchanged Blocks v1 pilot remains a legacy fixture, not silently upgraded policy.
Unknown keys fail closed. There are no shell commands, adapter paths, URLs,
credential selectors, reviewer identity overrides, or install/state roots.

`validate-manifest` performs syntax/policy checks offline. A valid manifest is
**not enrollment, identity verification, review PASS, or activation approval**.

## Trusted admission (library implemented; transport not implemented)

1. Resolve repository numeric ID and installation from the service-owned registry.
2. Read policy from the registry's approved base commit and content hash, not
   arbitrary PR-head configuration. Verify repository identity matches enrollment.
3. Record that policy version/hash with the exact review tuple and epoch.
4. A PR changing its manifest is reviewed under the previously approved policy.
   Policy promotion is a separate owner-authorized action; it invalidates affected
   in-flight evidence. Missing/mismatched/unapproved policy blocks admission.
5. Observe genuine exact-head CI after draft deferral and the quiet interval,
   then comprehensive OpenClaw, then comprehensive ClawSweeper. A missing or
   skipped rail is not PASS; no result authorizes merge.

`tools/trusted_admission.py` implements steps 1-4 offline: see
[trusted-admission.md](trusted-admission.md). Transport, the authenticated
service surface and the live registry are still absent; the legacy engine does
not yet ingest these manifests. Do not claim the manifest validator or the
admission library enforces live admission.

## Service-owned enrollment

Keep numeric repository identity, approved policy commit/hash, reviewer actor map,
App installation, webhook/adapter credential references, ingress and per-target
checkout/state/proof paths outside target Git. Each installation, webhook secret,
reviewer credential boundary and mutable store must be isolated. A target cannot
request another target's installation, read its evidence, or supply its reviewers.
No credentials or live deployment files are included in the scaffold.

## Gateway/client contract (planned; no server or plugin installed)

Clients may request an enrolled exact-tuple review with an idempotency identity,
read its status, and consume bounded typed progress events. The service derives
policy and reviewer authority from enrollment, never from client assertions.
Responses must include repository/PR/base/head/epoch, policy identity, current
state, rail conclusions, evidence references and blockers. Readiness is distinct
from merge authorization. Clients do not write verdicts, share the database,
submit arbitrary commands, or mint external rail checks.

Reconciliation/adjudication/repair remain separately authorized operations, not
implicit permissions of a generic request endpoint. Notification ownership must
be singular: a delivery outbox owns user sends; the new service should emit events.
The legacy direct notification adapter is preserved as compatibility code only.
