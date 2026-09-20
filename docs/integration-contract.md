# Repository manifest and service boundary v2

## Repository-owned requirements

A target may commit `.review-conductor.json` using
[`target-manifest.schema.json`](../contracts/target-manifest.schema.json).
The examples are proposals only; no target repository has been changed/enrolled.

The manifest contains only repository name, default branch, CI workflow name/path,
readiness-gated ClawSweeper dispatch, comprehensive scope, ordered required rails,
and human-only merge. V2 removes the fixed quiet period: every exact draft head
runs CI then OpenClaw; ready status enables ClawSweeper only after both clear.
Unknown keys fail closed. There are no shell commands, adapter paths, URLs,
credential selectors, reviewer identity overrides, or install/state roots.

`validate-manifest` performs syntax/policy checks offline. A valid manifest is
**not enrollment, identity verification, review PASS, or activation approval**.

## Trusted admission (library and source transport implemented; not deployed)

1. Resolve repository numeric ID and installation from the service-owned registry.
2. Read policy from the registry's approved base commit and content hash, not
   arbitrary PR-head configuration. Verify repository identity matches enrollment.
3. Record that policy version/hash with the exact review tuple and epoch.
4. A PR changing its manifest is reviewed under the previously approved policy.
   Policy promotion is a separate owner-authorized action; it invalidates affected
   in-flight evidence. Missing/mismatched/unapproved policy blocks admission.
5. Observe genuine exact-head CI, then comprehensive OpenClaw. Dispatch
   comprehensive ClawSweeper only while that exact head is ready. A missing or
   skipped rail is not PASS; no result authorizes merge.

When an operator has an authoritative GitHub read-back for a completed CI
`workflow_run` but cannot locate the original webhook receipt, the
`reconcile-workflow-run` source command admits that one bounded read-back. It
requires the exact repository/PR/base/head/run tuple, records a distinct
`github-readback` receipt, and is idempotent if the original delivery was
already processed. It never reruns CI, polls GitHub, dispatches either review
rail, or changes GitHub state. Conflicting reuse of a workflow-run identity
fails closed. On strict profiles the service admission hook is mandatory; the
standalone source command is therefore not a production activation path. This
is recovery evidence, not deployment or activation.

`tools/trusted_admission.py` implements the inert contract. The source-only
`tools/service_runtime.py` authenticates before parsing, rejects unknown App /
installation / numeric repository tuples, retrieves only the approved manifest
path at the pinned commit, requires the admitted manifest to agree with the
engine profile, and persists the policy binding in the same SQLite transaction
as the accepted `pull_request` delivery that established the head. `workflow_run`
and `issue_comment` deliveries never create bindings. Authenticated maintainer
`@ClawSweeper rereview` / `@clawsweeper re-review` comments may refresh
ClawSweeper on the current exact tuple after valid CI and comprehensive OpenClaw;
they do not promote policy, grant merge, or accept `@clawsweeper review` as an
alias. `pull_request.edited` remains unsupported. See
[ClawSweeper rereview](clawsweeper-rereview.md). Worker/check projection is blocked if a
current head lacks a current binding; the registry is re-read on every delivery
and tick. The same v2 registry may carry an optional exact-profile
`legacy_xapi` marker; omitted documents remain legacy absent, and
`trusted_enrollment_from_registry` consumes only that loaded field
after snapshotting and revalidating the exact base Registry dataclass
fields and each nested Enrollment and optional LegacyXapiMarker from
its own stored base-dataclass fields. Nested authority-bearing
strings and IDs must be exact builtins; reconstruction compares those
exact base values so a str subclass cannot synthesize repository, ID,
or legacy authority. `Registry.lookup` also requires those exact
builtin types at the lookup boundary. The service-profile repository
must be an exact admitted-scope string before omitted-marker absence
is treated as legitimate none. A matching enrollment still requires
a valid enabled core `review_policy` and the exact registry reviewer
mapping before Conductor present; missing or non-dict policy or
core config is broken. Unmatched enrollment keeps the existing
absent/legacy result. Inert registry document
validation/loading stays available here; live
`registry_provider` document-only worker ticks, `run_service_tick`
enrollment wiring, and queue/delivery consumption are stacked adapter
work. Notification send leases, reserved claim/send
fencing, and route-freshness guards used only for delivery are stacked
adapter work, not this routing/admission core.
See [trusted admission](trusted-admission.md).
No live registry, credential, HTTPS edge or deployment exists, so this is
qualified source behavior, not live admission.

The source-only launcher command
`tools/review_conductor_userland_launcher.py standalone` validates an external
mode-0600 registry against the SMCBD profile. `preflight` reports that
registry/bootstrap status and does not resolve credentials or invoke
`tools/standalone_supervisor.py`. `start` forwards already-prepared webhook
and GitHub App descriptors into the supervisor and never treats reviewed-profile
`onepassword.op_path` or runtime selectors as live secret sources. `health`
only queries the supervisor. None of these verbs resolve or start cloudflared.
Legacy
`start` remains the Blocks 9443 consumer. The supervisor invokes
`tools/service_entrypoint.py` for one exact SMCBD profile. Its child argv carries
only the profile and external-registry paths plus fixed command words; webhook
and GitHub App credentials move only through explicitly inherited descriptors.
The foreground supervisor exposes identity-bound local health/stop/restart
control, retains anonymous descriptor copies for explicit restart, and leaves a
crashed service failed until that restart is requested. The service runs in an
owned process session and inherits dedicated leader, generation, and
parent-lifetime descriptors alongside the credential descriptors. The entrypoint
makes the leader and parent-lifetime descriptors close-on-exec, while every
direct adapter command uses one authoritative wrapper that explicitly preserves
the generation descriptor and selector through `close_fds`. If the supervisor
exits or is killed, the service observes EOF on the parent-lifetime descriptor
and drains its still-owned session instead of remaining a listening orphan.
Entrypoint SIGINT/SIGTERM/SIGHUP request shutdown on a helper thread so the
serving loop cannot deadlock, and parent-loss uses a bounded full-session drain
that does not wait for an in-flight worker tick or admit further work.
That drain observes remaining owned-session descendants without reaping
worker-owned children, so adapter `subprocess.run` exit status stays intact, and
it is marked complete only after the owned-session drain succeeds. A signaling
error leaves the drain incomplete so the watcher can retry, including while
`worker.join` is blocked. Final service exit waits for that drain, including
when EOF arrives during `worker.join` and the worker then returns, so a daemon
watcher cannot be torn down mid-drain. Repeated parent-loss retries schedule
the HTTP shutdown helper once and retain only the first signaling failure.
Parent-liveness supervision continues after stop is requested until shutdown
actually completes; if the supervisor write end closes while worker.join is
still blocked, the service still drains its owned listener, lock, and
generation. Ingress bind records the listen host and port without
reverse-resolving through `getfqdn`, so macOS mDNS cannot stall readiness
before the owned listener exists.
Standalone launcher stop waits through the supervisor drain budget and treats
SIGHUP as orderly stop via `stop_standalone_child`; legacy Blocks `start`
keeps `stop_child`'s ten-second default. An installed caller that stops both
the conductor and tunnel through `stop_child` changes only the conductor call
to `stop_standalone_child` after this lands. This repository does not contain
a launchd unit; root prepares that host externally with KeepAlive=false, no
automatic retry, RunAtLoad=true, AbandonProcessGroup=false, and ExitTimeOut
longer than the complete standalone stack drain. Explicit
stop/restart keeps the unreaped leader and generation descriptor as race-free
identities, signals the process group, and reaps the leader only after every
inheritor has closed the generation descriptor; no numeric PGID is probed after
identity release. The entrypoint verifies the
supervisor's digest of the complete
normalized profile before registry or state access. Restart and stop control
wait longer than the shutdown bound; failed normal or exceptional shutdown
retains the active supervisor lock/control boundary, and startup connection
failures are not reported as stopped while that lock is held. Malformed control
commands are rejected without shutting the supervisor down. The control socket
is private from bind and unlinked from bind onward. Startup requires the default
`SIGCHLD` disposition, and every control frame has one absolute monotonic
deadline rather than a resettable per-read timeout. It neither provisions
credentials nor starts an HTTPS tunnel, and it is not installed or active.

## Service-owned enrollment

Keep numeric repository identity, approved policy commit/hash, reviewer actor map,
App installation, webhook/adapter credential references, ingress and per-target
checkout/state/proof paths outside target Git. The service implementation and
read-only dashboard may be shared, but each tenant's installation, webhook
secret, reviewer credential boundary, state, queue, proof and mutation authority
must be isolated. A target cannot
request another target's installation, read its evidence, or supply its reviewers.
No credentials or live deployment files are included. The inactive SMCBD
candidate pins App `4916376`, installation `161027021`, repository
`saari-co/openclaw-smcbd-suite`, repository ID `1366416798`, and authoritative
reviewer actors `spark-openclaw` / `saari-clawsweeper`. Enrollment remains
disabled. The owner-approved target-policy commit/hash (including any later
`POST12_MAIN` value) is derived during later deployment into an external
registry and is not invented here. Remaining activation blockers are live
GitHub/App, credential, registry, tunnel, producer, and protection work.

The dedicated SMCBD pilot App remains restricted to the `saari-co` account and
installed only on `openclaw-smcbd-suite`. The pilot does not require public or
"Any account" visibility. A shared App or broader visibility is a later explicit
isolation decision; future repository enrollment does not inherently require a
new App per repository.

Reviewer services own the original native reports and detailed findings as
evidence. Only Conductor publishes the authoritative rail check names after
validating the corresponding exact-revision evidence. For the native publication
route, Conductor also solely owns one idempotent projection comment containing
selected native public sections, its existing status labels and the explicitly
allowlisted native rating, priority, proof/media and merge-risk label families.
The accepted tuple/epoch/digest and current admission bind publication; native
prose and labels do not regrade, adjudicate or alter status authority. This
source-qualified boundary supersedes the earlier status-only/comment split;
the [rich publication contract](#rich-native-clawsweeper-publication-source-qualified-activation-held)
below defines its bounds and held activation gates, not deployment permission.
Check output must name the current stage, decision reason, exact head, workflow
run, and accepted artifact digest. Review-success is not merge authorization.
Fence the previous writer before a cutover; never operate competing
authoritative writers. Dashboard clients are read-only and no check result
grants merge authority.

## Gateway/client contract (planned; no server or plugin installed)

Clients may request an enrolled exact-tuple review with an idempotency identity,
read its status, and consume bounded typed progress events. The service derives
policy and reviewer authority from enrollment, never from client assertions.
Responses must include repository/PR/base/head/epoch, policy identity, current
state, rail conclusions, evidence references and blockers. Readiness is distinct
from merge authorization. Clients do not write verdicts, share the database,
submit arbitrary commands, or mint external rail checks.

Reconciliation/adjudication/repair remain separately authorized operations, not
implicit permissions of a generic request endpoint. Notification ownership must
be singular: a delivery outbox owns user sends; the new service should emit events.
The legacy direct notification adapter is preserved as compatibility code only.

## OpenClaw exact-tuple adapter command dependency (source-qualified)

`tools/review_conductor.py` `command_preview` emits the source-owned x-api
transport flags `--exact-tuple-contract review-conductor-openclaw-v1` and
`--review-epoch <bound non-negative review epoch>` exactly once, in that order,
only when the OpenClaw adapter declares `exact_tuple_contract` as that pinned
companion contract. Legacy profiles omit the field and keep the legacy queue
vector. Epoch 0 is the first valid generation. Payload JSON cannot select a
different generation. The x-api source-owned contract, not Conductor caller or
target policy, fixes `review_scope=comprehensive`,
`reviewer_actor=spark-openclaw`, and `native_max_priority=P3`; Conductor does
not pass actor, scope, or priority.
Generalized `review_policy` profiles materialize an OpenClaw terminal only when
Spark `REQUEST_STATUS.json` also carries `native_max_priority=P3`,
`applied_max_priority=P3`, and `exact_tuple_qualified` is `True` together with
the trusted comprehensive exact-tuple identity compared by type and value.
Those qualification fields are persisted on the terminal artifact and enforced
again at `runtime.bridge_openclaw` and `openclaw.terminal` internal-event
acceptance. Copied request `review_scope` while native execution remains P0 is
rejected. Missing, P0, conflicting, or false fields fail closed with no
artifact write for clean and findings results. JSON `false`/`true`/`1.0`/`"1"`
do not match integer identity fields. Legacy profiles without `review_policy`
stay compatible. Review prose and adapter exit code are not applied-P3
evidence.
This names an adapter command dependency only. It is not x-api or spark-dgx
publication, installation, attachment, source pin, or activation.


## Original OpenClaw report publication

Prospective collected OpenClaw terminals carry an optional closed
`original_report` receipt. `available` requires `ref` and lowercase `sha256`;
`missing`, `oversized`, or `invalid_text` carry only the status. The collector
preserves the original `review_output.txt` already fetched by the existing
Spark transport, separately from its status `PROOF.md`. Accepted UTF-8 output
is bounded to 24 KiB, copied unchanged under the exact request proof directory,
and checked against its digest at bridge acceptance and again at publication.
The fetch source is restricted to the transport-owned
`runs/spark-openclaw-autoreview-runs/<generated-lane-run-id>/` beneath the
configured source root. The generated fetch ID is not the review request ID:
`REQUEST_STATUS.json` from that opened directory must bind the exact action
before proof/report bytes are copied. Traversal, outside absolute paths and
symlinked descendants are rejected. Directory-descriptor traversal and
single-open, no-follow regular-file reads keep validation and copied bytes
bound to the opened directory/inode even when path names are replaced.
Missing or oversized output is explicitly unavailable, never a full-report or
clean-content claim. Terminal verdict/qualification remains independently bound
to repository/PR/base/head/epoch/request; report prose grants no authority.
Legacy artifacts that omit this field are not backfilled from nearby files.

The Conductor renders the original output literally inside its existing owned
OpenClaw check, including reviewer caveats, with the accepted digest. Native
text is not interpreted as Markdown, commands, policy, or links. A successful
review does not assert complete report publication when the receipt says the
report is unavailable. No external host, upload service, or model write
credential is introduced.

For an existing check, the App reads GitHub's check-run response and accepts its
`html_url` only if repository/check ID/name/head/App/external ID all match the
owned projection. That observed page becomes `details_url`; generic App,
repository, PR, foreign check and guessed Actions links are not substitutes.
A late-created check first persists its ID using the existing uncertain-create
fence; its next normal reconciliation binds the observed link. This never
retries creation because a link update failed. ClawSweeper retains its validated
run/artifact links; the external report-URL allowlist is unchanged. See GitHub's
[Checks API](https://docs.github.com/en/rest/checks/runs) for the existing
`output.text` and `html_url` surface.

## Rich native ClawSweeper publication (source-qualified; activation held)

The existing Conductor-owned projection comment can now present selected public
sections from the accepted native report: What This Changes, native overall /
proof / patch tiers and their original themed score scale, verification/evidence,
system context, a supplied safe Mermaid flowchart, findings, and next steps.
The presentation does not recompute grades, classify findings, adjudicate, or
change the four existing Conductor status-label meanings. Human-only merge is
unchanged. Producer provenance and synthetic previews are recorded in
[the restoration proof](../proof/rich-clawsweeper-publication-20260915/PROOF.md).

Prospective ClawSweeper terminal events retain the bridge-validated optional
`proof_sha256`. Rich publication requires this immutable accepted receipt, the
quality digest, and the ingested workflow's exact repository/PR/base/head/epoch,
run, proof path, scope and actor to agree. It reads the original report at the
existing external `clawsweeper/<run>/<PR>.md` path using no-follow directory
handles and one nonblocking regular-file descriptor, bounded to the native
512 KiB export limit. Digest/identity verification precedes current projection
writes. No parallel artifact store or target execution is added. Missing, changed,
malformed or oversized accepted files block publication; old receipts without
a retained digest and legacy Blocks profiles remain explicit compact summaries,
not inferred historical rich reports.

Only named public sections are projected. Raw frontmatter, work prompts, Close
Comment, GitHub Snapshot and telemetry are not dumped. Native prose is quoted,
HTML/control markers/fences/mentions are neutralized, and sections have explicit
excerpt limits. Mermaid is only supplied native flowchart text within a bounded,
noninteractive subset: no fabricated diagram, URLs, HTML, configuration or styling
commands. The comment has a conservative 48 KiB local byte budget; if exceeded,
a bounded unavailable notice replaces the rich portion rather than publishing
broken/partial Markdown as complete. The accepted digest and workflow artifact
link remain accessible, and full original evidence stays in the existing proof
store and original workflow artifact.

Native label ownership is separate from status authority. Explicit native fields
select the original rating family, triage P0–P3, `proof: sufficient`, screenshot /
video evidence labels and the eight native merge-risk labels. No free-form label
instructions, fallback score, invented priority or `proof: override` authority
is accepted. Missing optional metadata leaves that family unowned. Manual and
foreign labels outside the exact activated families are preserved. Scoped POST
add / DELETE one-label operations use the existing repository App permission and
admission guard; there is no destructive set-label operation or label bootstrap.
Native add/remove responses must confirm the requested result; an unavailable
label or rejected API operation is an explicit publication failure, not success.
Already-absent DELETE remains idempotent. See the existing GitHub
[issue label API](https://docs.github.com/en/rest/issues/labels).

Native report admission and presentation accept exactly the producer's six proof
statuses: `sufficient`, `missing`, `mock_only`, `insufficient`, `not_applicable`,
and `override`. Unknown and obsolete `not_needed` / `failed` / `required` proof
values are rejected before execution-failure, findings or maintainer precedence
can bypass validation. `mock_only` remains proof-deficient. Native `override`
records the producer's existing maintainer-proof evidence; it is not rewritten
as `sufficient`, cannot mint `proof: override` or `proof: sufficient`, and cannot
remove a human-owned override label. It does not bypass actual findings,
maintainer decisions, contributor action, rating/process gates, or human-only
merge. Clean content retains `owner_merge_authority`. The separate enrolled
ready-notification policy remains `sufficient`-only; neither new status qualifies
that notification. Stored historical summaries and legacy Blocks fallback remain
unchanged; this validation is for newly parsed native evidence, not a backfill.

Before native publication writes, the existing event log durably records the
exact tuple/digest and explicitly owned families. Repeated ticks update one
App-owned marker-bound comment and reconcile only label deltas. Interrupted
publication retains cleanup ownership. Closed or superseded tuples retract only
their journaled native families and mark their owned comment historical; they
cannot remove a foreign App's comment or non-owned labels. Existing status
cleanup and current-tuple publication admission remain separate and unchanged.

This is prospective source qualification, not deployed GitHub rendering. The
restoration draft must remain held until Suite **#4 → #6 → #5 → #7** land, then
be refreshed and requalified. No ready transition, reviewer dispatch, merge,
producer change, deployment, credential or protection change is authorized here.

## Cold exact-object hydration (source-qualified)

An admitted standalone service hydrates uncached PR objects through its existing
repository-scoped GitHub App client and Contents-read capability. It never uses
operator `gh` authentication, global Git config, target credential helpers, or
interactive prompts. Admission is checked before credential resolution, after
resolution, immediately before authenticated-fetch and object-import subprocess
launches, and after import. Existing supervisor generation
descriptors remain inherited throughout. Missing auth or revoked authority fails
closed without submitting a reviewer request. Legacy clients without this
capability may reuse already-present objects but cannot perform a cold fetch.
Unavailable legacy capability and local resource failures close the pending
action once; actual admission denial remains distinct and stops the tick.

The service-owned credential helper consumes one bounded installation token from
an anonymous pipe. Only the descriptor number and expected repository enter Git
configuration/argv. The helper accepts only HTTPS, `github.com`, the exact enrolled
repository path and `get`; it neither stores credentials nor follows fallback
authentication modes. Redirects are disabled. The token is never written to a
named or anonymous file, environment, diagnostic, proof or operator output. Git's
private credential-protocol pipe is its sole subprocess recipient.

Authenticated fetch runs in a disposable independent bare object store with a
clean HOME and allowlisted environment, not the target checkout. The current PR
ref must resolve to the admitted head, and the explicit base must be its ancestor.
A bounded, validated pack is then imported via `index-pack --strict`; checkout
HEAD, refs, origin and working tree are rechecked and remain unchanged. Temporary
files contain repository objects only. Network/fetch failures expose a closed
reason class, never raw Git output. This source qualification uses synthetic
credentials and local independent stores; it is not live private-GitHub proof.

Hydration Git subprocesses use a fixed exec wrapper, not `preexec_fn` in the
threaded service. Inherited `RLIMIT_FSIZE` limits each written file to 256 MiB;
`fetch.unpackLimit=0` keeps incoming objects packed. CPU time is limited to 180
seconds per process, alongside the existing 180-second command wall timeout.
After fetch, total stored object bytes must fit 256 MiB. A bounded-file
`cat-file --batch-all-objects` inventory rejects more than 100,000 objects or
512 MiB of aggregate expanded object bytes before repack or checkout import.
Repack uses one thread and a 64 MiB window; import also enforces Git's
`--max-input-size`. Linux additionally enforces 2 GiB per-process DATA/AS limits.

These are per-file/per-process and pre-import budgets, not a host quota or an
aggregate network-byte cap. Git may index/decompress objects in disposable
staging before the inventory gate. macOS does not consistently support DATA/AS
limits, so no hard macOS memory bound is claimed; transient staging/indexing
memory and simultaneous child totals are not bounded by the expanded-object
inventory. Over-budget repositories fail closed rather than receiving a partial
review. The limits do not change checkout refs, HEAD or worktree.
