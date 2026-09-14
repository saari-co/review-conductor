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
- Mutation authority: local source-only Copilot review `5200085128`
  round 1/2 repair, commit, and non-force push of this branch. Ledger
  remains 1/2; no Copilot or reviewer request.
- Starting exact head before this repair:
  `340c60e1c7c86a652d46a9339b60f7dd905fd132`.
- Not authorized: merge, deploy, authenticate, read live secrets, provision
  registry/tunnel, start/stop any service, change webhook/protection/account
  settings, edit PR #8/#11/#12 or product repos, create another PR, or later
  placeholder-writer fencing.

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
`health` only queries the supervisor. Argv, environment, logs, and tracked
files never receive credential values. None of these verbs resolve or
start cloudflared. Legacy `start` remains the Blocks 9443 consumer and
rejects any generalized `profile_id` before credential resolution.

The launcher/supervisor fail closed on inactive, absent, foreign,
permission-wrong, or profile-mismatched external enrollment. Tests use
synthetic resolver bytes, synthetic mode-0600 registry files, injected
subprocesses, and disposable paths only.

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
   not ready, and `auth_mode` bound to the selected capability set.

Mutants killed include enrollment-before-inherit, profile-selector
resolve, omitted `pass_fds`, legacy consumer launch, invented tunnel ID,
missing tunnel-name guard, omitted descriptor cleanup, literal-SMCBD-only
legacy rejection, preflight `ready` while waiting, and hard-coded
three-account auth metadata.

## Verification

Locally exercised interpreter: CPython 3.14.6 on macOS.

- Focused new suites and mutants — PASS (16 launcher tests, including 10
  precise mutants, plus the two profile regressions and empty-tunnel-name
  rejection).
- `make check` — PASS, including Blocks/userland/launcher/supervisor
  suites and the new suite-activation launcher file.
- `make build` — PASS.
- `python3 -m compileall -q tools tests scripts` — PASS.
- `scripts/check_provenance.py` — PASS for the 14 extracted files after
  destination-hash and adaptation updates.
- `actionlint` — not required; workflow bytes were unchanged.
- `git diff --check` — PASS on the unstaged source diff.

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
  SMCBD and Blocks product repositories, and PR #8/#11/#12 were untouched.
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

Publish this source slice as one draft PR. Do not request review, merge,
deploy, authenticate, provision registry/tunnel, or start any service.
After source lands, separately derive and promote the owner-approved
SMCBD target-policy commit/hash into the external registry.
