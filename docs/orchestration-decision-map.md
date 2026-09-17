# Issue #687 decision map

Source-linked map for later Review Conductor workstreams. This slice owns
enrollment-route and terminal notification eligibility only. It is not
activation, x-api removal, or a second notification system.

| Later workstream | Consumes | Must not weaken |
| --- | --- | --- |
| Versioned provider job/result contract | `openclaw_result` / `clawsweeper_result` and exact repository/PR/base/head/epoch identity | Structured results replace prose; unknown results stay fail-closed |
| Explicit structured findings | Third-set `adjudication_dispositions` (`required_fix`, `defer`, `reject_false_positive`, `human_gate`) | First two automatic rounds remain silent; deferrals stay linkable |
| Move result interpretation from x-api | `tools/orchestration_outcome.py` and the existing notification queue | No x-api runtime import; legacy-only repositories keep `legacy_xapi` |
| Move Spark queue/host integration to spark-dgx | `review_dispatch` on the Review Conductor route | Dual enrollment still forbids duplicate legacy dispatch |
| Versioned provider/runtime pin | Outcome `schema` plus extraction/provenance ledger | Rollback remains documented; this repo still cannot load target credentials |
| Enroll additional repositories | Route `review_conductor` vs `none` vs `fail_closed` | Broken enrollment is not unenrolled; neither enrollment stays no-review/no-notify |
| Remove obsolete x-api review infrastructure | Zero live `legacy_xapi` consumers proven | Human-only merge, exact-head rails, and two-cycle cap stay |

Canonical function: [`decide_orchestration_outcome`](../tools/orchestration_outcome.py).
Canonical schema: [`orchestration-outcome.schema.json`](../contracts/orchestration-outcome.schema.json).
Queue consumer: [`queue_notifications`](../tools/review_conductor_userland.py).
