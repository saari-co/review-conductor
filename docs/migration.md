# Extraction and migration status

## Completed in this scaffold

- Allowlisted source extraction from x-api commit
  `48036abf1649a6fbd1738d68b23fc235893e0b68` on
  `openclaw/review-conductor-generalization-managed`.
- Five Python engine/adapter modules and four profiles were initially copied.
  The launcher has a standalone anonymous-descriptor transport/cleanup repair.
  The engine now accepts an injected admission hook, and the inactive SMCBD
  profile records the verified App/installation identity; the provenance ledger
  records those adaptations. Legacy live adapter behavior remains unchanged.
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

1. Trusted enrollment, injected approved-policy loading, authenticated ingress,
   and atomic tuple/epoch binding exist as inactive source modules. Still open:
   live registry provisioning, credential transport, a qualified policy reader,
   and materializing approved policy as effective engine configuration.
2. Replace x-api `bin/smoky`/host-relative integration with qualified external
   adapters. The copied compatibility modules still describe the existing pilot;
   tests inject transports. They are not a portable activated service yet.
3. Add the separately reviewed executable service/client surface and event
   outbox. No Gateway plugin, Smoky dependency, webhook registration, scheduler,
   live token minting, check publication, checkout hydration, reviewer dispatch,
   or notification send is present in this slice.
4. Obtain exact-head external reviews of this new repo. It is not self-enrolled;
   CI success is not OpenClaw/ClawSweeper clearance.
5. Separately authorize each target's manifest commit, installation and isolated
   credential/proof/state provisioning. SMCBD's missing identities/producers and
   PR #3 CI/rail ownership blockers remain unresolved.
6. Provision isolated credential storage, then transfer credentials through the
   approved protected path. Start and qualify the service and HTTPS endpoint with
   GitHub webhooks disabled. Obtain a separate webhook-activation go/no-go, run
   the SMCBD shadow pilot while existing required-check bindings remain unchanged,
   and prove genuine reviews, replay handling and stale-evidence rejection.
7. Obtain a separate cutover go/no-go before transferring authoritative required
   checks to the Conductor. Fence the prior writer first. Only after SMCBD and its
   rollback are accepted, repeat for Blocks and prove cross-tenant isolation.
   Retire x-api ownership in a separately reviewed change. No dual active
   conductor writers.

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
