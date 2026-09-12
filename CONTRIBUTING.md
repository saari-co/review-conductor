# Contributing

Read [AGENTS.md](AGENTS.md), [SECURITY.md](SECURITY.md) and the architecture and
integration contracts before changing this security-sensitive foundation.

## Review boundary

- Work on one isolated task branch with one mutation owner. Open a draft PR;
  never push implementation directly to `main`, self-approve, or auto-merge.
- Run `make check` and `make build`. CI runs on draft updates using the event's
  exact head, read-only permissions, ephemeral hosted runners and no secrets.
- Supply the base/head SHAs, changed boundary, regression evidence, terminal
  check URLs, missing/skipped checks and remaining blockers. A new base/head
  invalidates prior evidence. CI green is not external review clearance.
- Request owner review only when authorized to do so. Reviewers do not inherit
  mutation, policy-promotion, adjudication, deployment or merge authority.
- Human merge authorization is separate from technical readiness. Do not create
  an approval or trusted-owner override record on behalf of either owner. See
  [bootstrap](docs/bootstrap.md).

## Ownership

[owners.json](.github/owners.json) maps the two designated human owners to GitHub
numeric actor IDs: @saariuslystoned and @saarius. Both had repository admin access
at the read-only inspection. Re-verify IDs and write access before enforcement;
never substitute display names, organization membership, bots, or chat claims.
This map is repository governance only, not the service enrollment/reviewer map.

[CODEOWNERS](.github/CODEOWNERS) covers **all files**, including itself, workflows,
schemas, tests, proof, scripts and documentation. GitHub's two entries mean
**either owner**, not two required approvals. The applied baseline requires one
eligible non-author code-owner approval on the normal path. It deliberately does
not require approval by someone other than the latest pusher.
For security/governance, workflow/check ownership, credential, policy/admission,
bootstrap or cutover changes, obtain explicit exact-head acknowledgement from
both designated owners before merge (an author's own acknowledgement is not a
GitHub review approval). This extra policy is human-verified, not enforced by
CODEOWNERS. Never claim two signatures merely because both names are listed.

Only the two mapped owners may use the protected-branch trusted-owner bypass.
Use it for an explicit emergency or maintainer override, never to manufacture
review PASS. Preserve the PR, exact-head CI and review evidence; record the actor,
reason, accepted risk and exact tuple in the PR conversation so GitHub's bypass
and audit events remain attributable. No team, App, bot or other user may bypass.

A PR cannot weaken the rules used to review itself. Evaluate governance changes
against the approved base; if no approved base exists, follow the bootstrap gate.
Future ownership changes require both existing owners, verified replacement
identity/access, and separate owner-authorized settings updates. Main protection
is currently applied and was read back as documented in `docs/branch-protection.md`;
tracked policy and PR-authored CI still cannot approve changes to themselves.

## Source and proof hygiene

Commit source, synthetic fixtures and sanitized proof only. The Git index guard
in `make check` rejects prohibited paths, binaries, symlinks/submodules and several
high-confidence credential markers, including force-added ignored files. It is a
bounded hygiene check, not exhaustive secret detection or trusted admission.
Review added files manually; `.gitignore` does not protect already tracked files.
Never use live credentials or actual runtime state to make tests pass. Keep local
build output under ignored `dist/` and test state in temporary directories.
