# SMCBD shadow-pilot activation handoff

Status: stacked live-adapter source candidate only; inactive; no secrets,
registry, tunnel, service or GitHub webhook configured by this repository. The
inactive admission core is reviewed separately in PR #3; this handoff belongs
to its stacked authority-bound adapter slice.

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
(refusing any symlink component and any forbidden root by device/inode, and
re-checking the forbidden roots against every walked directory after the walk),
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
the admitted manifest's repository, default branch, CI workflow name/path,
ClawSweeper readiness gate and merge policy agree exactly with that profile; promoting a manifest that
changes any of them requires the matching profile change first.

Every worker tick re-checks that the running profile's App, installation,
repository and reviewer actors are the registry's enrollment, and that each live
head's binding was admitted under the same policy, reviewer actors and
policy-governed profile fields that hold now; rotating reviewers or editing the
profile (including the ClawSweeper workflow, ref, adapter artifact prefix or
OpenClaw operator/transport/worktree shelf)
blocks projection until each head is re-admitted. Approved-policy transport
runs only for deliveries the engine has classified as new binding candidates,
never inside its write transaction, and is hash-verified against the enrollment;
closed, duplicate and stale deliveries never touch it. A transient GitHub
failure answers the webhook with 503 `dependency_unavailable` and writes
nothing, so the delivery can be redelivered from GitHub's delivery log or API
(GitHub does not retry automatically), while malformed or foreign deliveries
still answer 400. The delivery hook re-reads the registry inside the delivery
transaction and resolves the enrollment against the engine's freshly reloaded
profile before staging or persisting a binding; a profile with
`review_policy.enabled` no longer true fails closed everywhere. The tick's
opening gate is followed by an exact repository/PR/base/head/epoch guard
immediately before every current review side effect (each mutating GitHub call
after token minting, each OpenClaw dispatch, and each review-result notification
delivery), so a newer valid binding cannot authorize superseded work selected
for an older tuple. Superseded-check cleanup and unbound operator alerts use the
repository-level gate. Installation-token minting is synchronized
but deliberately not fenced by an old binding, because ingress must be able to
retrieve a newly promoted policy; the subsequent repository mutation is fenced
after token minting. Checkout hydration is fenced before the checkout is
touched and again immediately before a fetch. Both OpenClaw external commands
and every individual notification send receive their own fresh fence.
Fail-closed admission conditions are
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

The source-only launcher command
`tools/review_conductor_userland_launcher.py standalone` validates an external
mode-0600 registry against this profile. `preflight` reports registry and
bootstrap status only. `start` forwards already-prepared webhook and GitHub
App descriptors into `tools/standalone_supervisor.py` and does not read live
secrets from reviewed-profile `onepassword` selectors. `health` only queries
the supervisor. None of these verbs resolve or start cloudflared. Legacy
`start` remains the Blocks 9443 consumer and refuses this profile.
Standalone stop uses `stop_standalone_child`; Blocks/tunnel callers keep
`stop_child`. No launchd unit is stored here. The
supervisor passes those two credentials to `tools/service_entrypoint.py`
through inherited file descriptors while supplying only the non-secret profile
and registry paths plus fixed lifecycle verbs as arguments. It binds local health/stop/restart control to the complete
validated profile, registry path, tenant roots and loopback port; a crashed
service remains failed pending explicit restart. Explicit stop/restart also
holds inherited leader, generation, and parent-lifetime descriptors while
signaling the owned
service process group independently of leader exit, then reaps only after every
generation holder closes its descriptor. The service fails closed if the
parent-lifetime write end disappears. The close-on-exec leader descriptor
reports crashes without reaping on every supported host, and the shared adapter
command boundary preserves the generation descriptor through `close_fds`. The
entrypoint verifies
the supervisor's normalized-profile digest before registry or state access. Restart
and stop control wait longer than that shutdown bound. A failed shutdown
retains the supervisor lock/control boundary even after an unexpected loop
failure; startup connection failure while that lock is held is not reported as
stopped. The Unix socket is private from bind, not only after chmod. Startup
requires the default `SIGCHLD` disposition, and local control framing has one
absolute deadline that slow byte trickling cannot extend.
This is synthetic qualification, not an installed supervisor or activation.
Secret values must never be entered in chat,
command arguments, environment values, Git, logs, proof, PR comments or Actions
artifacts.

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
   their recovery tokens in protected bootstrap locations outside source, state
   and proof roots. Operators verify access with `op whoami` without reading
   secret values into logs. Do not use legacy `start` for the standalone
   profile; that command remains the Blocks 9443 consumer. The source-qualified
   `standalone` launcher command is the later deployment entry that connects
   the protected resolver output to the supervisor's inherited descriptor
   inputs. It is uninstalled and unstarted here.

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

Use the following staged sequence. Completing one stage does not authorize the
next stage.

### Stage 1: credential prerequisites and App permission

1. Open the App's **General** settings. Confirm it remains restricted to
   `saari-co`; do not change visibility or publish it to Marketplace.
2. Under repository permissions, add **Contents: Read-only**. Confirm the exact
   permission list above and no additional permissions. Keep event subscriptions
   to `pull_request` and `workflow_run` only.
3. Confirm installation `161027021` still uses **Only select repositories** and
   lists only `saari-co/openclaw-smcbd-suite`.
4. Generate the private key and transfer it with the 1Password procedure above.

### Stage 2: service and HTTPS qualification, webhook disabled

5. Provision the isolated service and proposed HTTPS route using the protected
   credentials. Keep the GitHub App webhook disabled and its URL empty.
6. Verify service health, TLS, loopback routing, exact deployed source, isolated
   state/queue/proof paths, and a synthetic signed POST without logging secrets.

### Stage 3: separately authorized webhook activation

7. Obtain an explicit SMCBD webhook-activation go/no-go.
8. Set the webhook URL to the qualified HTTPS URL, fill the independently stored
   webhook secret through 1Password, keep SSL verification enabled, then enable
   **Active** and save.
9. Use GitHub's Recent Deliveries view to confirm one authenticated delivery is
   accepted. Redeliver the same event and verify the service reports a duplicate
   without producing a second review action. Also prove stale-head evidence cannot
   advance or publish a current rail.

### Stage 4: separately authorized check cutover

10. Complete shadow qualification with existing required-check bindings unchanged.
11. Obtain a distinct cutover go/no-go. Fence every prior authoritative writer,
    then rebind only the qualified rail checks to the Conductor App in one reviewed
    change. Re-read protection and verify the exact issuers afterward.

Do not change branch protection during these actions. SMCBD's required `CI`,
`OpenClaw Review Rail` and `ClawSweeper Review Rail` remain bound to GitHub
Actions App `15368` until genuine App `4916376` exact-head checks have completed
the shadow qualification and a separate cutover is authorized.

## Activation prerequisites and authorization

Activation remains blocked until all are true:

- standalone source changes are reviewed and landed;
- App Contents permission is corrected;
- SMCBD `main` contains an owner-approved `.review-conductor.json`, and its exact
  commit/hash — including any later `POST12_MAIN` value — is derived and
  promoted into the external registry during deployment, not invented here;
- the external registry's `reviewers` block names the source-bound actors
  `spark-openclaw` and `saari-clawsweeper` exactly;
- dedicated 1Password service accounts, private key, webhook secret and tunnel
  connector are provisioned;
- the registry-aware standalone supervisor and launcher source are independently
  reviewed and an exact landed revision is selected for deployment;
- the isolated HTTPS edge and local service health are verified;
- a human explicitly authorizes deployment and SMCBD shadow-mode activation.

Installation alone is never activation. Fixture PASS is never review clearance.
During shadow qualification, App `4916376` may publish its genuine rail checks,
but Actions App `15368` remains the required issuer. OpenClaw and ClawSweeper
retain native detailed findings/comments; the Conductor alone publishes the two
authoritative rail check names after validating their exact-revision evidence.
Verify clean, findings,
timeout, replay, stale head/base/epoch, cross-installation and policy-promotion
cases before proposing cutover.

The target repository still carries its v1 manifest with
`quiet_seconds=600`. This launcher slice does not edit that repository. After
launcher qualification, a separate target-repository change must promote the
owner-approved scheduling-v2 manifest and record its exact commit/hash in the
external registry before activation.

## Authority-bound maintenance

The standalone entrypoint exposes only the two state-recovery mutations needed
by the live adapters. Both re-read the service-owned registry, require every live
head to retain a current approved-policy binding, then re-check the selected PR's
binding inside the same SQLite transaction that performs the mutation. Neither
command reads GitHub or notification credentials.

Preview before applying:

```text
python3 tools/service_entrypoint.py --profile PROFILE --registry REGISTRY --dry-run retry-openclaw --pr PR
python3 tools/service_entrypoint.py --profile PROFILE --registry REGISTRY --dry-run reconcile-notification --pr PR --channel CHANNEL --disposition sent --confirm provider-delivery-observed
python3 tools/service_entrypoint.py --profile PROFILE --registry REGISTRY --dry-run reconcile-notification --pr PR --channel CHANNEL --disposition retry --confirm provider-nondelivery-observed
```

Repeat the selected command with `--apply` instead of `--dry-run` only after the
preview identifies the intended exact current tuple. Registry/profile revocation,
a stale or missing binding, a closed tuple, ambiguous notification state, or an
action that is not currently failed aborts without changing state. There is no
generic SQL, adjudication, merge, check-publishing or reviewer-dispatch maintenance
command on this entrypoint.

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
