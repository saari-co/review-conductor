# Security boundary

**Offline scaffold only; no supported live service or deployment.** The zipapp
contains only manifest validation. Extracted engine/runtime/launcher modules and
historical profiles exist for regression coverage, are not service entrypoints,
and must not be invoked against real credentials, repositories or deployments.
Some legacy profiles retain active pilot defaults for byte-for-byte compatibility;
that is not standalone enrollment. Do not deploy directly from `tools/`.

## Trust and authority

Treat PR code, manifests, workflow files, artifacts, comments and client input as
untrusted. Repository policy cannot select credentials, installations, trusted
reviewers, commands or another repository's state. A valid manifest grants no
authority. Approved base-policy loading, policy-hash binding and trusted enrollment
are **not implemented**. Neither are the authenticated API/outbox or qualified
standalone live adapters; do not expose the legacy internal-event CLI as an API.

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
