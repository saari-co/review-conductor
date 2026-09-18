# Issue #687 decision map

Source-linked map for later Review Conductor workstreams. This slice owns
enrollment-route and terminal notification eligibility only. It is not
activation, x-api removal, or a second notification system.

| Later workstream | Consumes | Must not weaken |
| --- | --- | --- |
| Versioned provider job/result contract | `openclaw_result` / `clawsweeper_result` and exact repository/PR/base/head/epoch identity | Structured results replace prose; unknown results stay fail-closed for routing and still notify blocked |
| Explicit structured findings | Third-set `adjudication_dispositions` (`required_fix`, `defer`, `reject_false_positive`, `human_gate`) | First two automatic rounds remain silent; cycle 2 still allows scoped `required_fix`; deferrals stay linkable. Copilot 5242972219's cycle-2 "unbounded repairs" finding is rejected as inconsistent with this saturating 2/2 ledger |
| Move result interpretation from x-api | `tools/orchestration_outcome.py` and the existing notification queue | No x-api runtime import or dispatch; `legacy_dispatch` stays false; legacy-only repositories keep `route=legacy_xapi` / handoff required |
| Move Spark queue/host integration to spark-dgx | `review_dispatch` on the Review Conductor route | Dual enrollment still forbids duplicate legacy dispatch |
| Versioned provider/runtime pin | Outcome `schema` plus extraction/provenance ledger | Rollback remains documented; this repo still cannot load target credentials |
| Enroll additional repositories | Route `review_conductor` vs `none` vs `fail_closed` | Broken enrollment is not unenrolled; it notifies blocked without review or legacy dispatch |
| Remove obsolete x-api review infrastructure | Zero live `legacy_xapi` consumers proven | Human-only merge, exact-head rails, and the saturating 2/2 automatic-repair ledger stay |

Canonical function: [`decide_orchestration_outcome`](../tools/orchestration_outcome.py).
Canonical schema: [`orchestration-outcome.schema.json`](../contracts/orchestration-outcome.schema.json).
Trusted enrollment resolver: [`resolve_trusted_enrollment`](../tools/orchestration_outcome.py).
Inert registry helper: [`trusted_enrollment_from_registry`](../tools/service_runtime.py).
Live queue, delivery, `run_tick` route suppression, and
`run_service_tick` enrollment wiring are stacked adapter work; this
core does not change those runtime entry functions. The v2 registry may carry an optional
exact-profile `legacy_xapi` marker; omitted means legacy absent.
`trusted_enrollment_from_registry` snapshots and revalidates exact
base Registry dataclass fields and each nested Enrollment and optional
LegacyXapiMarker from its own stored base-dataclass fields, then reads
only those loaded values. Nested authority-bearing strings and IDs
must be exact builtins; reconstruction compares those exact base
values so a str subclass cannot synthesize repository, ID, or legacy
authority.
The service-profile repository must be an exact admitted-scope string
before omitted-marker absence is treated as legitimate none. Userland
`enabled` / `blockers` flags do not select the route. External
registry document validation/loading is separate from strict
Conductor ingress admission. Public
enum-like inputs require exact builtin strings; persisted
readiness/quality flags accept only integer `0`/`1`. A long-lived
GitHub App client resets or replaces its admission authority guard on
every route transition. Identical current eligibility decisions stay
deduped by route, reason, and eligibility. Notification send leases,
reserved claim/send fencing, complete current-event identity, and
route-freshness guards used only for delivery are stacked adapter
work. `human_gate=true` precedes merge-ready and silent
nonterminal dispatch. Impossible state/rail/result tuples, including
mismatched rails, fail closed before any enrollment-route
short-circuit, review_dispatch, or notification eligibility is
calculated. The persisted-row adapter applies the same rail-aware
validation and maps inconsistent stored state/rail data to typed
unknown results. `closed` / `closed_merged` are terminal, silent,
and non-dispatchable even when enrollment is broken; closed-state
handling precedes enrollment short-circuits. Unknown/result/tuple
coherence is validated after that closed exception and before
unenrolled or legacy short-circuits. `notification.eligibility`
has no `fail_closed` value; representable failures use `blocked`. The
public schema accepts only producer-emittable eligibility/kind/channel
combinations.
