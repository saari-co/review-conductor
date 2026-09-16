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
The launcher `standalone` command is source-only qualification of that same
descriptor contract into `tools/standalone_supervisor.py`. It validates the
external registry before any resolver call, inherits only webhook and GitHub
App descriptors, and never starts cloudflared. It is not installed and does
not activate the profile.

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
process group independently of whether the leader has already exited. Distinct
inherited descriptors track the leader and the complete service generation, and a
reciprocal parent-lifetime descriptor lets the service fail closed and drain that
still-owned session when the supervisor disappears. Entrypoint stop signals
schedule `serve_forever` shutdown off the serving thread, and parent-loss drain
does not wait for an in-flight worker tick. Parent-loss observes remaining
owned-session descendants without reaping worker-owned children, and marks that
drain complete only after it succeeds so a signaling error can retry while
`worker.join` is blocked. Parent-liveness supervision stays
active after stop is requested until shutdown completes, so a later supervisor
disappearance still performs the owned-session drain. Ingress bind does not
reverse-resolve the listen address, so startup cannot stall on DNS before the
owned listener is ready. The standalone launcher also
handles SIGHUP and waits through the supervisor's full TERM-then-KILL drain
budget via `stop_standalone_child`; legacy Blocks `start` keeps
`stop_child`'s original ten-second default. The helper is the one installed
conductor-stop adaptation; it is not a service-manager API, and no launchd
unit is stored in Git. The
leader descriptor is made close-on-exec by the entrypoint, so every supported
host can report leader exit without reaping and releasing its process-group
identity. Every direct adapter subprocess is launched through the generation
boundary with the generation descriptor explicitly preserved despite
`close_fds`. Shutdown sends SIGTERM and then bounded SIGKILL, and reaps the
leader only after the generation descriptor reaches EOF; it never probes or
signals a numeric PGID after releasing that identity. A missing, escaped or
undrained generation fails closed instead of starting a new one. Before registry
or state access, the entrypoint also requires
the normalized profile digest calculated by the supervisor to match its one
loaded configuration, so a replaced profile cannot change the advertised tenant.
The supervisor rejects startup unless it owns the default `SIGCHLD`
disposition, preventing inherited auto-reap or custom handlers from releasing
the leader identity behind its back.
The control socket is private from bind under a temporary restrictive umask and
is unlinked from bind onward, including chmod failure. A failed shutdown keeps
the original supervisor lock and control socket active so another start cannot
overlap the surviving group. Unexpected control-loop failure uses the same
retained cleanup state and cannot unwind either owner until shutdown succeeds.
Stop/restart control waits longer than the maximum
group-shutdown bound; connection failure while the lock is held is reported as
starting/unavailable, never stopped. Non-string control commands are rejected
without terminating the service.
Control framing uses one absolute monotonic deadline, so trickled bytes cannot
extend the single-threaded request boundary indefinitely.
Anonymous storage may still be disk-backed and is not a secure-erasure guarantee.
