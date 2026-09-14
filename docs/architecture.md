# ADR 001: Independent conductor; repository-owned requirements

Status: accepted direction from the owner on 2026-09-11; deployment deferred.

## Decision

Review Conductor has its own repository, runtime, build and release lifecycle.
Targets declare review requirements in a versioned `.review-conductor.json`,
similar to a repository-local reviewer configuration. One multi-tenant service
implementation reads approved configuration; it does not accumulate each
product's implementation. Initial enrollment is
`saari-co/openclaw-smcbd-suite`, followed by `dinkuskit/blocks`. Tenants may
share implementation and read-only dashboard visibility, but credentials, App
installations, enrollment, mutable state, queues, proof and mutation authority
remain isolated. Adding a later repository is explicit configuration/enrollment
work, not a new service fork.

| Owner | Responsibility |
| --- | --- |
| Target repository | CI workflow identity, default branch, readiness gate and required rails |
| Conductor service | Exact tuple/epoch state, admission, adapter sequencing, bounded repair routing, audit evidence |
| Service enrollment registry | Numeric repo identity, App installation, approved policy SHA/hash, authoritative reviewer actors, credential references and isolation domains |
| GitHub Actions | Deterministic CI |
| Reviewer services | Native detailed findings/comments plus comprehensive exact-tuple evidence through qualified adapters |
| Gateway / Smoky | Narrow client requests and status explanations; no conductor database access |
| Human owner | Policy promotion, adjudication authority and merge authorization |

No shared database, source import, npm workspace, submodule, or coordinated
release with x-api/Gateway/Smoky. A conductor outage blocks review clearance,
not Gateway startup or unrelated agent work. No automatic merge endpoint.

The canonical tuple is repository + PR + base SHA + head SHA + review epoch.
Any base/head change invalidates evidence. Webhook authentication precedes
parsing; replays are idempotent; conflicting deliveries fail closed. Uncertain
non-idempotent dispatch is reconciled, not blindly retried. Two repair cycles
exhaust automatic repair; reviewers never become mutation owners.

The Conductor is the sole writer of the authoritative `OpenClaw Review Rail`
and `ClawSweeper Review Rail` checks after it validates reviewer-native evidence.
A completed exact-head review can project review-success while merge stays
human-only. Check output names the current stage, decision reason, exact head,
workflow run, and accepted artifact digest. The Conductor may publish one
idempotent projection comment and only ClawSweeper status labels it owns;
reviewers retain native detailed findings. Dashboard projections are read-only,
and merge authority remains human-only.

## Practical transition

Reuse the tested Python/SQLite implementation, not a new workflow framework.
Python standard-library unittest, zipfile and argparse provide the scaffold's
build/test tools. No new hosted platform or paid dependency is introduced.
Legacy module/schema names remain for compatibility, not architectural ownership.
Host-specific launch, Spark and notification adapters are preserved for regression
coverage but are not exposed by the packaged scaffold. Qualify replacements at
the adapter boundary before a separate live migration.
