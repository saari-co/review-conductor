# Extraction and migration status

## Completed in this scaffold

- Allowlisted source extraction from x-api commit
  `48036abf1649a6fbd1738d68b23fc235893e0b68` on
  `openclaw/review-conductor-generalization-managed`.
- Five Python engine/adapter modules and four profiles initially copied byte-for-byte.
  The launcher now has a standalone anonymous-descriptor transport/cleanup repair;
  the other engine modules and profile bytes remain unchanged.
- Four regression suites relocated, with root/fixture path adjustments; one
  synthetic key marker is assembled from bytes with the same runtime value for
  source hygiene. The versioned extraction/provenance ledger records these adaptations.
- Independent offline manifest contract, tests, zipapp build, and CI.
- `provenance.json` is a versioned extraction/provenance ledger: it records
  `source_branch`, the source commit, original hashes and current destination
  hashes/adaptations. Recording a branch name does not push that branch. The
  referenced x-api source branch remains local/unpublished (remote ref absent
  on the read-only 2026-09-11 check); this repair did not change it.

The Spark lane text fixture preserves an existing static upstream assertion; it
is not an executable adapter or proof of an installed Spark producer. Regression
fixtures preserve public/non-secret deployment metadata but are not runtime
configuration. The build excludes all historical profiles and legacy launchers.

## Not completed / prerequisites for migration

1. Implement trusted repository-manifest loading and approved policy-hash binding
   to tuple/epoch; migrate profile configuration without changing legacy behavior.
2. Replace x-api `bin/smoky`/host-relative integration with qualified external
   adapters. The copied compatibility modules still describe the existing pilot;
   tests inject transports. They are not a portable activated service yet.
3. Implement/version the authenticated service/client surface and event outbox.
   No Gateway plugin, Smoky dependency, webhook registration or scheduler is added.
4. Obtain exact-head external reviews of this new repo. It is not self-enrolled;
   CI success is not OpenClaw/ClawSweeper clearance.
5. Separately authorize each target's manifest commit, installation and isolated
   credential/proof/state provisioning. SMCBD's missing identities/producers and
   PR #3 CI/rail ownership blockers remain unresolved.
6. Plan a gated pilot cutover with exact deployed source, genuine artifact proof,
   state/schema compatibility and rollback. Only after acceptance retire x-api
   ownership in a separately reviewed change. No dual active conductor writers.

## Untouched

x-api source/branch, accidental worktree, detached live conductor, Gateway,
Smoky runtime, Blocks/SMCBD repositories, PR #3, branch protection, credentials,
staging, production, merge and adjudication state.

## Historical evidence

The source contract and Swarm Delivery boundary were inspected at the pinned
x-api SHA. Its existing exact tuple/epoch and human-only merge rules remain.
Historical pilot location/health evidence is in x-api's
`proof/review-conductor-generalization-20260911/PROOF.md`; it was not reactivated
or re-probed by this scaffold task.
