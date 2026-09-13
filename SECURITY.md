# Security boundary

**Offline, inactive qualification source only; no deployed service.** The zipapp
contains only manifest validation. `tools/service_entrypoint.py` is an executable
but unpackaged qualification entrypoint; the other extracted engine/runtime/launcher
modules and historical profiles exist for regression coverage and are not service
entrypoints. None may be invoked against real credentials, repositories or
deployments without the separately authorized pilot procedure.
Some legacy profiles retain active pilot defaults for byte-for-byte compatibility;
that is not standalone enrollment. Do not deploy directly from `tools/`.

## Trust and authority

Treat PR code, manifests, workflow files, artifacts, comments and client input as
untrusted. Repository policy cannot select credentials, installations, trusted
reviewers, commands or another repository's state. A valid manifest grants no
authority. `tools/trusted_admission.py` implements the inert enrollment and
policy-binding contract. `tools/service_runtime.py` adds authenticated GitHub
ingress, exact App/installation admission, approved-policy retrieval and atomic
binding persistence; `tools/service_entrypoint.py` requires an enabled profile,
a same-user external registry with mode exactly 0600 outside source, checkout,
state and proof roots (re-read on every delivery and tick), and
descriptor-delivered credentials. `tools/standalone_supervisor.py` is the
source-only foreground supervisor for that entrypoint. It accepts only profile
and registry paths plus fixed lifecycle verbs in argv, copies two inherited
credential descriptors into bounded anonymous files, and passes only descriptor
numbers through an allowlisted child environment. These
sources are not deployed. A general client API and delivery outbox remain **not
implemented**; do not expose the legacy internal-event CLI as an API. A registry
document is service configuration and must never be committed here or read from
a reviewed repository.

Preserve repository/PR/base/head/epoch binding, authentication before webhook
parsing, replay protection, stale/conflicting evidence rejection, isolated state,
bounded repairs and human-only merge. Credentials belong in service-owned
protected storage. Gateway and Smoky are future narrow clients, not co-owners of
state, releases or build dependencies. No shared mutable stores or dual
check writers. Review evidence is not a merge or adjudication decision.

## Prohibited repository material

Never commit real secrets or auth-bearing output: passwords, access/refresh or
installation tokens, private keys/cert bundles, cookies, recovery codes, webhook
secrets, vault/bootstrap material, `.env` files, credential exports or auth logs.
Never commit runtime databases/WALs, raw webhook/event payloads, production review
artifacts, checkouts, inboxes/outboxes, live enrollment/configuration, service
units, tunnel credentials or deployment bundles. Use only explicit synthetic
fixtures. Sanitized task proof may live in `proof/`; live service proof must not.
Do not attach sensitive evidence to PRs, issues, Actions logs or artifacts.

The tracked-file guard is intentionally limited to path/type/size and selected
credential signatures. A green guard does not certify absence of all secrets.
PR code can modify its own checks; independent human review of the exact diff
and trusted base policy remains necessary. Do not give PR CI secrets, write
permissions, self-hosted runners, privileged `pull_request_target` execution,
untrusted `workflow_run` artifact execution, or deployment jobs.

## Reporting and response

Contact @saariuslystoned or @saarius through an existing authenticated private
channel; disclose the minimum reproduction and affected revision, without secret
values. No security email or private GitHub advisory channel is claimed to be
configured. If no private channel is available, request one without posting the
sensitive details publicly. Maintainers coordinate incident containment and
rotation through the credential owner; deleting a file or rewriting Git does not
revoke a leaked credential. Do not conduct live exploitation, rotate credentials,
rewrite history or change deployments without the applicable authorization.

## Legacy launcher descriptor repair (offline qualification only)

Resolved runtime values are bounded to 1 MiB and passed via explicitly inherited
file descriptors, never consumer argv/environment values or log output. The
launcher uses the standard-library `TemporaryFile`, verifies a regular file with
zero links **before** writing any value, sets mode 0600, and rewinds before child
startup. Hosts unable to supply that anonymous storage fail closed. Anonymous
storage can be disk-backed: this is not a RAM-only or secure-erasure guarantee.
Only descriptor numbers and `/dev/fd/N` references enter consumer argv/env.
Partial preparation/spawn failures close parent descriptors; a failed tunnel
spawn terminates/kills and reaps the already-created conductor child.

This repairs a legacy transport deadlock; it does not qualify live cloudflared,
1Password, service lifecycle, credentials or deployment. Historical 1Password
bootstrap/resolver code remains regression-only, including its service-account
environment flow; the standalone package still excludes all launcher code.

## Standalone supervisor boundary (offline qualification only)

The standalone supervisor owns one SMCBD profile and its state-root lock and
mode-0600 Unix control socket. Control requests carry an exact non-secret identity
digest covering the complete validated profile configuration, profile/registry
paths, repository/App/installation IDs, state/checkout/proof roots and loopback
port. A mismatched or changed profile or registry path cannot stop or restart
that process. `start` runs the supervisor in the
foreground; `health`, `stop` and explicit `restart` use the local socket.
A crashed child remains failed and is never automatically restarted.

The caller must already have supplied distinct webhook-secret and GitHub-App-key
descriptors under the profile's two configured environment names. The supervisor
does not resolve 1Password, accept credential values in argv/environment, start
a tunnel, install a service unit or activate the profile. It closes the incoming
descriptors after creating verified anonymous copies, rewinds those copies before
each explicit child start, and closes them plus the socket on every exit path.
The service starts in its own process session; stop and restart signal that owned
process group, then bound termination, kill and reap of its leader so an active
adapter subprocess is not intentionally carried into the next generation.
Anonymous storage may still be disk-backed and is not a secure-erasure guarantee.
