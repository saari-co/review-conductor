# SMCBD suite activation launcher proof — 2026-09-14

## Identity and authority

- Repository: `saari-co/review-conductor`.
- Immutable starting/base commit:
  `f42875beeeab4cc1e82b701a692ff38ec8ac4a64` (tree
  `c5d7ebd5cc841b4d69c29102651d84ea85cffb4d`).
- Branch: `openclaw/smcbd-suite-activation-launcher-v1`.
- Head: the commit that contains this proof and
  `candidate-manifest.json`.
- Captured: `2026-09-14`.
- Mutation authority: owner-authorized frozen post-cap repair of
  Copilot review `5200380692` inline `4007417174` only. Ledger remains
  terminal **2/2**; no Copilot or reviewer request.
- Starting exact head before this repair:
  `21285c7cadcfa5f22dbda7d1bc00a14b1a75e3ac`.
- Not authorized: merge, deploy, authenticate, read live secrets, provision
  registry/tunnel, start/stop any service, change webhook/protection/account
  settings, edit PR #8/#11/#12 or product repos, create another PR, mark
  ready, or later placeholder-writer fencing.

## Qualified boundary

The suite core profile now source-binds authoritative reviewer actors
`openclaw=spark-openclaw` and `clawsweeper=saari-clawsweeper`.
`review_policy.enabled` and userland `enrollment.enabled` remain false.
The stale source blocker that those identities were not named is removed.
Remaining blockers are live App Contents permission, credentials, an
external registry, owner-promoted target-policy commit/hash (including any
later `POST12_MAIN` value), tunnel/ingress, producers, and protection
rebinding.

Enabled standalone source readiness no longer invents or requires a
deployment-only tunnel ID. An isolated public hostname is still required.
The committed profile keeps `tunnel.tunnel_id` null.

`tools/review_conductor_userland_launcher.py standalone` validates an
external same-user mode-0600 registry against the SMCBD profile.
`preflight` only validates that registry and bootstrap status; it does not
resolve credentials or invoke the supervisor. `start` forwards
already-prepared webhook and GitHub App descriptors into verified unlinked
mode-0600 anonymous regular files bounded to 1 MiB, then invokes
`tools/standalone_supervisor.py` with explicit `pass_fds` and
descriptor-number environment values. Reviewed-profile
`onepassword.op_path` and runtime selectors are not live secret sources.
Standalone start owns SIGINT/SIGTERM before supervisor spawn; the outer
cleanup path stops and reaps that child across the spawn and immediate
post-spawn window and restores prior handlers. `health` only queries the
supervisor. Argv, environment, logs, and tracked files never receive
credential values. None of these verbs resolve or start cloudflared.
Legacy `start` remains the Blocks 9443 consumer and rejects any
generalized `profile_id` before credential resolution.

The launcher/supervisor fail closed on inactive, absent, foreign,
permission-wrong, or profile-mismatched external enrollment. Tests use
synthetic resolver bytes, synthetic mode-0600 registry files, injected
subprocesses, and disposable paths only.

## Frozen 2/2 lifecycle repair

Required fix, Copilot review `5200380692` inline `4007417174`:
`start_standalone` previously spawned the supervisor child before
SIGINT/SIGTERM handlers were installed. Handlers are now installed and
owned before `Popen`. An outer cleanup path covers the full spawn and
immediate post-spawn window so a signal during startup or after `Popen`
returns deterministically stops/reaps the child and restores every prior
handler. The credential-descriptor `ExitStack` boundary and no-secret-leak
guarantees are unchanged.

## Deferred dispositions (review 5200380692)

These findings remain deferred and were not implemented:

- inline `4007417115`: health duplicate response validation;
- inline `4007417218`: generalized `tunnel_name` type/bounds;
- suppressed inherited-descriptor `OSError` normalization;
- suppressed migration wording.

Round 1/2 review `5200085128` remains repaired at starting head
`21285c7cadcfa5f22dbda7d1bc00a14b1a75e3ac`.

## Direct tests and mutants

Focused `SuiteActivationLauncherTests` plus profile regressions and
precise disposable-copy mutants:

1. no credential resolution before absent/mismatched/inactive enrollment
   rejection;
2. wrong repo/App/install/actor/policy profile mismatch;
3. changed profile `onepassword.op_path`/runtime selectors cannot redirect
   resolution; descriptors and secret-like values stay out of
   argv/env/log/tracked files;
4. partial inherited-descriptor preparation/spawn cleanup;
5. launcher → standalone supervisor argv/`pass_fds`/environment
   integration without a tunnel capability or profile-selector resolve;
6. Blocks legacy 9443 compatibility and rejection of any generalized
   `profile_id` before resolver;
7. enabled standalone source readiness without a tunnel ID, while empty
   `tunnel_name` is rejected;
8. standalone preflight top-level `waiting_for_human` when bootstrap is
   not ready, and `auth_mode` bound to the selected capability set;
9. SIGINT/SIGTERM during the `Popen`/startup call stops and reaps the
   child and restores prior handlers;
10. SIGINT/SIGTERM immediately after `Popen` returns and before the wait
    loop stops and reaps the child and restores prior handlers;
11. spawn exception restores prior handlers;
12. successful/normal exit restores handlers and reaps the child once.

Mutants killed include enrollment-before-inherit, profile-selector
resolve, omitted `pass_fds`, legacy consumer launch, invented tunnel ID,
missing tunnel-name guard, omitted descriptor cleanup, literal-SMCBD-only
legacy rejection, preflight `ready` while waiting, hard-coded
three-account auth metadata, and handler-after-spawn / missing outer
startup cleanup ordering.

## Verification

Locally exercised interpreter: CPython 3.14.6 on macOS.

- Focused new suites and mutants — PASS (20 launcher tests, including 11
  precise mutants, plus the two profile regressions and empty-tunnel-name
  rejection).
- `make check` — PASS, including Blocks/userland/launcher/supervisor
  suites and the standalone start lifecycle regressions.
- `make build` — PASS.
- `python3 -m compileall -q tools tests scripts` — PASS.
- `scripts/check_provenance.py` — PASS for the 14 extracted files after
  destination-hash and adaptation updates.
- `actionlint` — not required; workflow bytes were unchanged.
- `git diff --check` — PASS on the unstaged source diff and on
  `f42875beeeab4cc1e82b701a692ff38ec8ac4a64`...working-tree.

## Live limitations and untouched boundaries

- Source-qualified only. The profile is uninstalled and unstarted.
- No live 1Password, GitHub App, webhook, registry, tunnel, or service
  process was invoked.
- `POST12_MAIN` and the exact target-policy hash are derived during later
  deployment into an external registry. They are not invented here.
- Anonymous temporary storage may be disk-backed and is not secure
  erasure.
- GitHub App settings, webhooks, subscriptions, credentials, Cloudflare,
  branch protection, required checks, x-api, the live Dinkus service,
  SMCBD and Blocks product repositories, and PR #8/#11/#12 runtime or
  protection state were untouched.
- Fixture PASS is not a deployed review PASS.

## Exact supported commands

Placeholders are only for runtime-derived registry and later
owner-promoted policy values. The committed profile path is source-owned.

```text
python3 tools/review_conductor_userland_launcher.py \
  --config contracts/review-conductor/openclaw-smcbd-suite-userland.json \
  standalone --registry /absolute/external/registry.json preflight

python3 tools/review_conductor_userland_launcher.py \
  --config contracts/review-conductor/openclaw-smcbd-suite-userland.json \
  standalone --registry /absolute/external/registry.json start --apply

python3 tools/review_conductor_userland_launcher.py \
  --config contracts/review-conductor/openclaw-smcbd-suite-userland.json \
  standalone --registry /absolute/external/registry.json health

python3 tools/standalone_supervisor.py \
  --profile contracts/review-conductor/openclaw-smcbd-suite-userland.json \
  --registry /absolute/external/registry.json health
```

Launcher preflight only validates registry/bootstrap, start forwards already-prepared inherited descriptors, and health only queries. The
committed profile remains inactive, so those launcher commands fail
closed until a later authorized deployment enables the profile and
supplies a matching external registry. The direct supervisor health path is a
local query: when no lock/socket exists it can return a successful
`stopped` payload without enrollment validation and must not be
overstated as the launcher fail-closed gate. These commands do not
start a tunnel or activate webhooks.

## Next safe action

Do not request review, merge, deploy, authenticate, provision
registry/tunnel, or start any service. After this exact head's hosted CI
is observed, separately derive and promote the owner-approved SMCBD
target-policy commit/hash into the external registry.
