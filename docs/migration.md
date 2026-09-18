# Extraction and migration status

## Completed in this scaffold

- Allowlisted source extraction from x-api commit
  `48036abf1649a6fbd1738d68b23fc235893e0b68` on
  `openclaw/review-conductor-generalization-managed`.
- Five Python engine/adapter modules and four profiles were initially copied
  byte-for-byte. The launcher now has a standalone anonymous-descriptor
  transport/cleanup repair and a narrow SMCBD `standalone` command that
  validates an external registry, resolves only webhook and GitHub App
  capabilities, and invokes `tools/standalone_supervisor.py`. Legacy `start`
  still launches the Blocks 9443 consumer and cloudflared. The runtime,
  userland loader and inactive SMCBD profile are now explicitly adapted for
  trusted policy transport, the verified App/installation candidate, and
  source-bound reviewer actors `spark-openclaw` / `saari-clawsweeper`; the
  Blocks profiles remain unchanged. An enabled standalone profile no longer
  invents or requires a deployment-only tunnel ID.
- Four regression suites relocated, with root/fixture path adjustments; one
  synthetic key marker is assembled from bytes with the same runtime value for
  source hygiene. The versioned extraction/provenance ledger records these adaptations.
- Independent offline manifest contract, tests, zipapp build, and CI.
- A native standalone foreground supervisor now invokes the registry-aware
  entrypoint with inherited webhook/App descriptors, an allowlisted child
  environment, inherited generation-lifetime, leader, and parent-lifetime
  descriptors, an entrypoint-verified
  exact SMCBD config/state identity and fail-closed
  health/start/stop/restart behavior. The service drains its owned generation
  when that parent-lifetime write end closes, including after stop is requested
  while an in-flight worker.join is still blocked, and does not return until that
  drain completes if EOF arrives during join. Ingress bind does not wait
  on reverse DNS before the owned listener is ready. The 1Password-aware launcher now has a
  source-only `standalone` path: `preflight` validates registry/bootstrap only,
  `start` forwards inherited webhook/App descriptors into that supervisor, waits
  through the supervisor drain budget via `stop_standalone_child`, treats SIGHUP as orderly stop, and
  `health` only queries it. Neither path is installed, and neither starts a
  tunnel. This source repair is not a deployed host change. A launchd unit
  remains root-owned and external; this repository does not add a service
  manager or a real unit file.
- `provenance.json` is a versioned extraction/provenance ledger: it records
  `source_branch`, the source commit, original hashes and current destination
  hashes/adaptations. Recording a branch name does not push that branch. The
  referenced x-api source branch remains local/unpublished (remote ref absent
  on the read-only 2026-09-11 check); this repair did not change it.

The Spark lane text fixture preserves an existing static upstream assertion; it
is not an executable adapter or proof of an installed Spark producer. Regression
fixtures preserve public/non-secret deployment metadata but are not runtime
configuration. The build excludes all historical profiles and legacy launchers.

OpenClaw `command_preview` includes the source-owned exact-tuple contract
selector and the persisted review epoch only when the OpenClaw adapter declares
the pinned `exact_tuple_contract` capability. Legacy profiles omit those flags
so the tracked Spark fixture parser still accepts the queue vector. Generalized
`review_policy` consumers in `collect_openclaw_terminals` require Spark status
`native_max_priority=P3`, `applied_max_priority=P3`, and
`exact_tuple_qualified is True` with type-and-value identity comparison before
any terminal artifact write; those fields are persisted and enforced again at
the OpenClaw bridge and internal terminal-event acceptance. Legacy profiles
without `review_policy` stay compatible. That is a Conductor adapter command
and evidence-consumer dependency only. It does not attach, pin, publish,
install, or activate the x-api transport or a spark-dgx applied-P3 attestation
source.

## Completed for issue #687 slice 1 (source-only; not deployed)

Deterministic terminal notification eligibility now lives in Review Conductor
as `review-conductor.orchestration-outcome.v1`. The existing userland
notification queue consumes
[`decide_orchestration_outcome`](../tools/orchestration_outcome.py); there is
no second notification system and no x-api runtime dependency. Silent first
and second automatic repair rounds, third-set adjudication without a human
gate, rail suppression, merge-ready, blocked/human-gate, unenrolled-none, and
fail-closed broken/unknown outcomes are source-qualified. Representable
fail-closed enrollment, unknown state/result, and equivalent invalid
orchestration states keep dispatch suppressed (`legacy_dispatch` remains
false) and use the blocked notification path; the queue delivers that
concise blocked message instead of raising or passing. `run_tick` resolves
trusted/service-owned enrollment at tick start so Review Conductor,
legacy-only, dual, unenrolled, and broken semantics are runtime behavior.
Hydration, action draining, result collection, and review stages run only
on the Review Conductor route. Caller payloads cannot grant enrollment,
and fail-closed copy prefers the canonical reason over stale persisted
blocker text. Pending notification rows are revalidated against the
current head and trusted enrollment before send; close, supersession,
an enrollment-route change, or a missing/partial/legacy decision
identity retires them. Only an exact current schema, route, reason, and
notification may remain eligible, and identical current decisions stay
deduped. `closed` and `closed_merged`
stay silent and non-dispatchable even when trusted enrollment is broken.
`run_service_tick` wires registry-owned enrollment into `run_tick` and
does not infer legacy/none/dual/broken from userland activation flags.
The v2 registry document may name one exact-profile `legacy_xapi`
marker; existing documents that omit it stay Conductor-only (legacy
absent). `load_registry` rejects malformed or ambiguous marker forms.
`trusted_enrollment_from_registry` consumes only that loaded field
after snapshotting and revalidating exact base Registry dataclass
fields and each nested Enrollment and optional LegacyXapiMarker from
its own stored base-dataclass fields. Nested authority-bearing
strings and IDs must be exact builtins; reconstruction compares those
exact base values so a str subclass cannot synthesize repository, ID,
or legacy authority. The service-profile repository
must be an exact admitted-scope string before omitted-marker absence
is treated as legitimate none. `service_entrypoint.registry_provider`
loads and validates the external registry document without applying
`require_profile_enrolled`; webhook ingress remains strict while the
production worker can represent Conductor, legacy-only, none, dual,
and broken routes. Pending notification claim/send is bound to the
expected current state and complete canonical decision, so a webhook
transition between eligibility and send retires the stale row.
Impossible state/rail/result tuples, including mismatched rails,
fail closed before enrollment-route short-circuits, dispatch, or
notification eligibility. Unknown/result/tuple coherence is validated
after closed-state handling and before unenrolled or legacy
short-circuits. The persisted-row adapter applies the same rail-aware
validation and maps inconsistent stored rows to typed unknown results
so the queue can notify blocked once. `human_gate=true` precedes merge-ready and silent nonterminal dispatch.
Notification event identity includes canonical route/reason/eligibility
so a superseded pending row retires while the current blocked decision
delivers exactly once. `repair_cycle` is the
saturating ledger for the first two broad automatic rounds: cycle 2 still
allows a scoped `required_fix` route, head change, and exact-head rerun
without a third automatic round or ledger reset. Terminal messages are
`<repo>#<pr> ready to merge` or `<repo>#<pr> blocked — <reason>`. Legacy-only
output names `route=legacy_xapi` / handoff required and does not dispatch
x-api. Later #687 workstreams are sequenced in
[orchestration-decision-map.md](orchestration-decision-map.md).

## Not completed / prerequisites for migration

1. Trusted enrollment, approved base-policy loading and policy-hash binding now
   have an authenticated source integration (`tools/service_runtime.py`) and an
   inactive entrypoint. Still open: provision the external registry, add the
   target manifest to an approved base commit, materialize the admitted manifest
   as the effective engine profile (today the two must agree exactly or binding
   fails closed), and activate an isolated service.
2. Qualify the remaining Smoky runtime integration. The standalone entrypoint
   contains its own periodic worker loop, so it needs no external scheduler or
   Gateway plugin; however, OpenClaw dispatch still executes the configured
   `spark.smoky_path` transport for the configured Spark target. The copied
   compatibility modules still describe the existing pilot and tests inject
   transports. They are not a portable activated service yet. The queue command
   now names the source-qualified exact-tuple contract flags; x-api attachment
   and source pin, plus spark-dgx applied-P3 attestation source/install, remain
   separate unpublished work.
3. Implement/version the general authenticated client surface and event outbox.
   GitHub webhook ingress exists; no Gateway plugin, external scheduler or
   webhook registration is added. The live OpenClaw dispatch path retains the
   explicit Smoky executable/runtime dependency described above.
4. Obtain exact-head external reviews of this new repo. It is not self-enrolled;
   CI success is not OpenClaw/ClawSweeper clearance.
5. Separately authorize each target's manifest commit, installation and isolated
   credential/proof/state provisioning. SMCBD's source-bound reviewer actors are
   now named; the external registry, owner-approved target-policy commit/hash
   (including any later `POST12_MAIN` value), live App Contents permission,
   producers, and PR #8/#11/#12 CI/rail ownership blockers remain unresolved.
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

The supervisor is new repository-native source, not an extracted x-api file.
The versioned extraction ledger therefore retains the original hashes and
adaptations for all 14 imported files; its verifier must remain green.

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
