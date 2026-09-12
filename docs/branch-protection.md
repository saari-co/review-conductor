# Main protection baseline — APPLIED AND READ BACK

## Current read-only observation

On 2026-09-11 (America/New_York), GitHub reports `main.protected=true`. The
classic protection endpoint returns the baseline described below and repository
rulesets remain empty. Both designated owners resolve to the numeric IDs in
`.github/owners.json` and are the only users allowed to push or dismiss reviews.
This observation was read-only; no settings were changed by the source repair.

Observed CI checks are `Python 3.11 / ubuntu-latest`,
`Python 3.12 / ubuntu-latest`, `Python 3.12 / macos-latest`, and aggregate `CI`.
All are issued by GitHub Actions (`app_id: 15368`, slug `github-actions`). The
aggregate runs with `always()` and fails unless the entire matrix succeeds.
Only that aggregate needs to be required; do not bind legacy target rail names.

## Applied classic protection body

[main-protection.proposed.json](main-protection.proposed.json) is retained under
its original review filename and matches the applied baseline read back from
`main`. It is an inert review artifact, not a script or authorization to call the
API again or to change repository settings.

- Require strict/up-to-date `CI`, pinned to GitHub Actions App 15368.
- Require PRs, one eligible CODEOWNER approval, stale approval dismissal,
  approval after the latest reviewable push, and resolved review conversations.
- Restrict pushes/merges and review dismissal to the two human owners; no App,
  team, or review bypass allowances. Enforce rules for administrators.
- Forbid force pushes/deletions. Keep branch unlocked; do not require linear
  history or fork syncing. No auto-merge, new tokens, or deployment hooks.

One approval is deliberate: with two owners an owner-authored PR has only one
other eligible owner. CODEOWNERS means either owner, not both. The additional
both-owner exact-head acknowledgement for sensitive changes is a documented
human gate, not something this GitHub configuration guarantees. A latest pusher
cannot satisfy the last-push approval. Authors/pushers must leave an eligible
independent owner or the PR blocks; do not weaken rules to unblock it.

## Limitations and later verification

PR #1's base has no CODEOWNERS; follow the explicit bootstrap decision in
[bootstrap.md](bootstrap.md). The applied review requirement therefore does not
retroactively make this PR's new CODEOWNERS authoritative for its own review.
Re-read and preserve/merge current settings before any later authorized update;
never overwrite from this recorded body. Reconfirm owner access and exact-head
issuer/check names. Rejected direct/bypass writes, stale/missing/failed CI and
missing-owner-review behavior still require a separately approved safe mutation
test; no such mutation probe was performed in this source-only repair.

App pinning prevents another App from satisfying `CI`; it does not prevent a PR
from weakening its own Actions workflow or creating a same-App check name.
Human review of workflow changes and trusted-base admission are still needed.
Comprehensive external reviews remain missing. Add App-owned external requirements
only after qualified standalone adapters, genuine checks and separate owner
authorization; never fabricate placeholder PASS or silently grant a bootstrap
exception. Protecting this repo does not rebind Blocks or SMCBD protection.
