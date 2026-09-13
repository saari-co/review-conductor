# Repository manifest and service boundary v2

## Repository-owned requirements

A target may commit `.review-conductor.json` using
[`target-manifest.schema.json`](../contracts/target-manifest.schema.json).
The examples are proposals only; no target repository has been changed/enrolled.

The manifest contains only repository name, default branch, CI workflow name/path,
readiness-gated ClawSweeper dispatch, comprehensive scope, ordered required rails,
and human-only merge. V2 removes the fixed quiet period: every exact draft head
runs CI then OpenClaw; ready status enables ClawSweeper only after both clear.
Unknown keys fail closed. There are no shell commands, adapter paths, URLs,
credential selectors, reviewer identity overrides, or install/state roots.

`validate-manifest` performs syntax/policy checks offline. A valid manifest is
**not enrollment, identity verification, review PASS, or activation approval**.

## Trusted admission (library and source transport implemented; not deployed)

1. Resolve repository numeric ID and installation from the service-owned registry.
2. Read policy from the registry's approved base commit and content hash, not
   arbitrary PR-head configuration. Verify repository identity matches enrollment.
3. Record that policy version/hash with the exact review tuple and epoch.
4. A PR changing its manifest is reviewed under the previously approved policy.
   Policy promotion is a separate owner-authorized action; it invalidates affected
   in-flight evidence. Missing/mismatched/unapproved policy blocks admission.
5. Observe genuine exact-head CI, then comprehensive OpenClaw. Dispatch
   comprehensive ClawSweeper only while that exact head is ready. A missing or
   skipped rail is not PASS; no result authorizes merge.

`tools/trusted_admission.py` implements the inert contract. The source-only
`tools/service_runtime.py` authenticates before parsing, rejects unknown App /
installation / numeric repository tuples, retrieves only the approved manifest
path at the pinned commit, requires the admitted manifest to agree with the
engine profile, and persists the policy binding in the same SQLite transaction
as the accepted `pull_request` delivery that established the head. `workflow_run`
deliveries never create bindings. Worker/check projection is blocked if a
current head lacks a current binding; the registry is re-read on every delivery
and tick. See [trusted admission](trusted-admission.md).
No live registry, credential, HTTPS edge or deployment exists, so this is
qualified source behavior, not live admission.

The source-only `tools/standalone_supervisor.py` invokes
`tools/service_entrypoint.py` for one exact SMCBD profile. Its child argv carries
only the profile and external-registry paths plus fixed command words; webhook
and GitHub App credentials move only through explicitly inherited descriptors.
The foreground supervisor exposes identity-bound local health/stop/restart
control, retains anonymous descriptor copies for explicit restart, and leaves a
crashed service failed until that restart is requested. The service runs in an
owned process session and inherits dedicated leader and generation descriptors
alongside the credential descriptors. The entrypoint makes the leader descriptor
close-on-exec, while every direct adapter command uses one authoritative wrapper
that explicitly preserves the generation descriptor and selector through
`close_fds`. Explicit stop/restart keeps the unreaped leader and generation
descriptor as race-free identities, signals the process group, and reaps the
leader only after every inheritor has closed the generation descriptor; no
numeric PGID is probed after identity release. The entrypoint verifies the
supervisor's digest of the complete
normalized profile before registry or state access. Restart and stop control
wait longer than the shutdown bound; failed normal or exceptional shutdown
retains the active supervisor lock/control boundary, and startup connection
failures are not reported as stopped while that lock is held. Malformed control
commands are rejected without shutting the supervisor down. The control socket
is private from bind and unlinked from bind onward. Startup requires the default
`SIGCHLD` disposition, and every control frame has one absolute monotonic
deadline rather than a resettable per-read timeout. It neither provisions
credentials nor starts an HTTPS tunnel, and it is not installed or active.

## Service-owned enrollment

Keep numeric repository identity, approved policy commit/hash, reviewer actor map,
App installation, webhook/adapter credential references, ingress and per-target
checkout/state/proof paths outside target Git. The service implementation and
read-only dashboard may be shared, but each tenant's installation, webhook
secret, reviewer credential boundary, state, queue, proof and mutation authority
must be isolated. A target cannot
request another target's installation, read its evidence, or supply its reviewers.
No credentials or live deployment files are included. The inactive SMCBD
candidate pins App `4916376`, installation `161027021`, repository
`saari-co/openclaw-smcbd-suite` and repository ID `1366416798` while retaining
explicit activation blockers.

The dedicated SMCBD pilot App remains restricted to the `saari-co` account and
installed only on `openclaw-smcbd-suite`. The pilot does not require public or
"Any account" visibility. A shared App or broader visibility is a later explicit
isolation decision; future repository enrollment does not inherently require a
new App per repository.

Reviewer services own their native detailed findings and comments. Only the
Conductor publishes the authoritative rail check names after validating the
corresponding exact-revision evidence. Fence the previous writer before a
cutover; never operate competing authoritative writers. Dashboard clients are
read-only and no check result grants merge authority.

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
