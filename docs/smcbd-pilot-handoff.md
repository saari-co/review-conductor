# SMCBD shadow-pilot activation handoff

Status: source candidate only; inactive; no secrets, registry, tunnel, service or
GitHub webhook configured by this repository.

## Verified non-secret identity

| Field | Value |
| --- | --- |
| GitHub App | `Saari Review Conductor SMCBD` |
| App slug | `saari-review-conductor-smcbd` |
| App ID | `4916376` |
| Installation ID | `161027021` |
| Installation account | `saari-co` |
| Repository | `saari-co/openclaw-smcbd-suite` |
| Repository ID | `1366416798` |
| Webhook path | `/github/webhook` |

Installation `161027021` was browser/API-verified as selected-repository scope
containing only `saari-co/openclaw-smcbd-suite`. The App is not operational:
webhooks are disabled, its URL is empty, and no private key or webhook secret has
been generated.

The inactive profile pins those IDs. Its required App permission contract is:

- Actions: read/write (exact-tuple ClawSweeper dispatch and artifact reads)
- Checks: read/write (rail projection)
- Contents: read-only (approved `.review-conductor.json` retrieval at a pinned commit)
- Pull requests: read/write (PR identity/metadata and PR-label endpoint authority)
- Metadata: read-only

No administration, deployment, environment, members, organization, secrets,
workflow-management or merge authority is permitted. **Contents: read-only is
not currently present in the GitHub registration and must be added before any
activation.**

## Service-owned files and selectors

The live enrollment registry is an external same-user regular file with mode
exactly `0600`, never a target-repository file. The entrypoint refuses a registry
located inside this source tree, the target checkout, or the state/proof roots,
canonicalizes only the ancestors, walks them with held directory descriptors
(refusing any symlink component and any forbidden root by device/inode),
requires the held parent to be the very directory that was canonicalized (same
device/inode, so a real directory renamed into place is refused) and a same-user
directory that is not group/world writable, opens
the original leaf name relative to that held parent without following symlinks
(so a leaf swapped for a symlink is refused, never canonicalized), treats a
symlink loop as an unavailable registry, opens the leaf non-blocking so a FIFO
cannot stall startup, validates and reads the opened descriptor,
and re-reads and re-validates the file on every delivery and worker tick, so a
promotion or revocation takes effect without restart and a registry that stops
validating fails every delivery and tick closed. It must contain exactly the repository/name/ID,
App/installation/account and an owner-promoted default-branch policy commit plus
the SHA-256 of the exact `.review-conductor.json` bytes, and the authoritative
OpenClaw/ClawSweeper reviewer actors. The loader accepts only
the two owner-approved repository names; the initial deployed registry should
contain SMCBD only. `dinkuskit/blocks` is added after the pilot under a separate
installation and migration decision.

The engine still reads its rules from the core profile. A policy binds only when
the admitted manifest's repository, default branch, CI workflow name/path, quiet
period and merge policy agree exactly with that profile; promoting a manifest that
changes any of them requires the matching profile change first.

Every worker tick re-checks that the running profile's App, installation,
repository and reviewer actors are the registry's enrollment, and that each live
head's binding was admitted under the same policy, reviewer actors and
policy-governed profile fields that hold now; rotating reviewers or editing the
profile (including the ClawSweeper workflow, ref or adapter artifact prefix)
blocks projection until each head is re-admitted. Approved-policy transport
runs only for deliveries the engine has classified as new binding candidates,
never inside its write transaction, and is hash-verified against the enrollment;
closed, duplicate and stale deliveries never touch it. A transient GitHub
failure answers the webhook with 503 `dependency_unavailable` and writes
nothing, so the delivery can be redelivered from GitHub's delivery log or API
(GitHub does not retry automatically), while malformed or foreign deliveries
still answer 400. The delivery hook re-checks the engine's freshly reloaded
profile against the enrollment before staging or persisting a binding, and the
tick's opening gate is re-run before every mutating GitHub call (check
creation/update, ClawSweeper dispatch) through the client's authority guard, so
a revocation or profile edit after the gate stops the rest of that tick. Fail-closed admission conditions are
retried on the next tick; any other worker failure (database, filesystem,
unexpected) stops the whole service with exit status 2 rather than leaving
ingress accepting deliveries that nothing will act on. The GitHub App client
refuses any permission map that is not one of the two closed allowlists.

Non-secret credential selectors are already isolated in the inactive profile:

| Capability | Runtime selector |
| --- | --- |
| GitHub webhook verification | `op://Review Conductor SMCBD Suite Webhook Runtime/Review Conductor SMCBD Suite Webhook/secret` |
| GitHub App private key | `op://Review Conductor SMCBD Suite GitHub App Runtime/Review Conductor SMCBD Suite GitHub App/private-key.pem` |
| Cloudflare connector | `op://Smoky Review Conductor SMCBD Suite Cloudflare Tunnel/SMCBD Suite Cloudflare Tunnel/connector-token` |

The launcher resolves each capability through its separate recovery/service-
account boundary and transfers values to the child only through inherited file
descriptors. Secret values must never be entered in chat, command arguments,
environment values, Git, logs, proof, PR comments or Actions artifacts.

### Approved credential transfer procedure

1. In the 1Password desktop app, create the three named runtime vault/items and
   exact fields above. Keep each capability in its own service-account domain.
2. In GitHub App settings, generate one private key only after the GitHub runtime
   item exists. Import the downloaded PEM into the `private-key.pem` field using
   the 1Password desktop UI; do not paste it into chat or an agent tool. Remove
   the downloaded plaintext file after verifying the stored item can be read by
   the dedicated service account.
3. Generate the webhook secret inside 1Password in the webhook runtime item.
   Use the 1Password browser extension to fill the GitHub Webhook secret field;
   the value never passes through agent context.
4. Provision the Cloudflare connector token directly into its dedicated runtime
   item through Cloudflare/1Password UI. Do not reuse the Blocks connector.
5. Create the three least-privileged 1Password service accounts and place only
   their recovery tokens in the profile's protected bootstrap locations. The
   launcher consumes those tokens to resolve the runtime selectors; operators
   verify access with `op whoami` without reading secret values into logs.

## HTTPS ingress and source qualification

The source listener binds only to loopback and accepts only POST
`/github/webhook`, JSON bodies within the configured limit, `pull_request` and
`workflow_run` events, a safe delivery ID, and a valid `X-Hub-Signature-256`.
Authentication occurs before JSON parsing, enrollment lookup or policy reads.
Delivery replay is idempotent; reuse with changed event/content fails closed.
The App ID, installation ID, repository name and numeric repository ID must all
match service enrollment before engine state can commit. The policy binding is
stored atomically with the exact repository/PR/base/head/review-epoch tuple, and
worker/check projection stops if the binding is missing or stale.

The proposed isolated public edge is:

`https://smcbd-suite-review-1366416798.ztoned.com/github/webhook`

This hostname is a proposal, **not a live endpoint**. Before GitHub browser
configuration, provision a dedicated Cloudflare tunnel/DNS route to loopback
port `9444`, confirm TLS validation, ensure no peer profile shares the hostname,
port, tunnel, state, checkout, proof, inbox or credential boundary, and verify a
synthetic signed POST reaches the service without logging its body or signature.

## Exact remaining GitHub browser actions

Perform these only after the source is reviewed/merged, the isolated service and
HTTPS edge are healthy, the target policy is owner-approved, and activation is
explicitly authorized:

1. Open the App's **General** settings. Confirm visibility is **Any account**;
   do not publish it to Marketplace.
2. Under repository permissions, add **Contents: Read-only**. Confirm the exact
   permission list above and no additional permissions. Keep event subscriptions
   to `pull_request` and `workflow_run` only.
3. Confirm installation `161027021` still uses **Only select repositories** and
   lists only `saari-co/openclaw-smcbd-suite`.
4. Generate the private key and transfer it with the 1Password procedure above.
5. Set the webhook URL to the qualified HTTPS URL, fill the independently stored
   webhook secret through 1Password, keep SSL verification enabled, then enable
   **Active** and save.
6. Use GitHub's Recent Deliveries view to confirm one authenticated delivery is
   accepted. Redeliver the same event and verify the service reports a duplicate
   without producing a second review action.

Do not change branch protection during these actions. SMCBD's required `CI`,
`OpenClaw Review Rail` and `ClawSweeper Review Rail` remain bound to GitHub
Actions App `15368` until genuine App `4916376` exact-head checks have completed
the shadow qualification and a separate cutover is authorized.

## Activation prerequisites and authorization

Activation remains blocked until all are true:

- standalone source changes are reviewed and landed;
- App Contents permission is corrected;
- SMCBD `main` contains an owner-approved `.review-conductor.json`, and its exact
  commit/hash is promoted into the external registry;
- authoritative OpenClaw and ClawSweeper reviewer actor identities are recorded
  in the external registry's `reviewers` block and the engine profile agrees
  exactly;
- dedicated 1Password service accounts, private key, webhook secret and tunnel
  connector are provisioned;
- the isolated HTTPS edge and local service health are verified;
- a human explicitly authorizes deployment and SMCBD shadow-mode activation.

Installation alone is never activation. Fixture PASS is never review clearance.
During shadow qualification, App `4916376` may publish its genuine rail checks,
but Actions App `15368` remains the required issuer. Verify clean, findings,
timeout, replay, stale head/base/epoch, cross-installation and policy-promotion
cases before proposing cutover.

## Rollback

Before authoritative cutover, rollback is: disable the GitHub webhook, stop the
isolated service/tunnel, revoke the App private key and capability service-account
tokens, rotate the webhook secret, and preserve sanitized state/proof for review.
Actions-owned required checks remain unchanged, so this rollback cannot weaken
current protection.

After a future authorized cutover, first fence the conductor writer, then rebind
protection only to a previously qualified issuer/workflow in the same reviewed
change. Never restore the x-api and standalone conductors as simultaneous
authoritative writers. Retain historical checks as evidence; do not relabel them
PASS or delete them to hide a failed migration.
