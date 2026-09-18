# ClawSweeper rereview source proof

Date: 2026-09-18
Issue: saari-co/review-conductor#19
Base: origin/main `8cc4094e88c8cd04c5b7da28e0c6a9ef054ea69c`
Branch: `openclaw/clawsweeper-rereview-v1`

## Claim

Review Conductor now admits authenticated maintainer `@ClawSweeper rereview`
and `@clawsweeper re-review` comments, refreshes same-head ClawSweeper
evidence without replaying a prior terminal, and waits for current-tuple CI
plus comprehensive OpenClaw on a new head. `@clawsweeper review` is not an
alias. `pull_request.edited` is not a trigger. Merge stays human-only.

This is source-qualified fixture proof, not a deployed or live review PASS.

## Commands

```text
python3 tests/test_clawsweeper_rereview.py
python3 tests/test_review_conductor.py
python3 tests/test_review_conductor_userland.py
python3 tests/test_review_conductor_activation.py
python3 tests/test_review_conductor_profiles.py
make check
make build
```

## Coverage

- same-head proof refresh and stale superseded completion, including when
  the prior findings dispatch stays `dispatched` in the same `created_at`
  second as the rereview action
- rereview does not advance the PR-event source watermark, so a later
  same-head PR delivery remains current; uncertain acknowledgement
  publication and abandoned acknowledgement claims stay `reconcile_required`
- new-head prerequisite wait
- unauthorized, bot, non-PR, and `@clawsweeper review` ignores/refusals
- duplicate delivery, duplicate comment, and in-flight wait
- exhausted two-cycle repair budget
- unsupported `pull_request.edited`
- GitHub command → acknowledgement + new dispatch → current check projection
- `issue_comment` deliveries never create policy bindings

## Gates

- no merge, deploy, live App subscription, or secret material
- live verification blocked; see ACTIVATION.md
