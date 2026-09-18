# ClawSweeper rereview activation checklist

Status: **blocked**. This slice qualifies source behavior only. It does not
authorize live GitHub App, webhook, producer, protection, deploy, or merge
changes.

## Already qualified in source

- Authenticated `issue_comment.created` admission through Conductor
- Canonical `@ClawSweeper rereview` and alias `@clawsweeper re-review`
- Explicit refusal of `@clawsweeper review`, body-edit auto-trigger, bots,
  non-maintainers, non-PR comments, duplicates, in-flight reviews, stale
  completions, and exhausted repair budgets
- Same-head evidence refresh that reuses still-valid CI/OpenClaw
- New-head prerequisite gating
- Acknowledgement action plus existing rail-check presentation
- Human-only merge preserved

## Remaining live work (separate authority each)

1. Add the `issue_comment` GitHub App event subscription for the enrolled App.
   Do not add `issues` permission. Do not change live subscriptions from this PR.
2. Confirm the webhook remains the isolated Conductor HTTPS edge and that
   Recent Deliveries show signed `issue_comment` acceptance without logging
   secrets.
3. Prove one maintainer command on a ready exact head creates a new ClawSweeper
   attempt and a current check, then prove a replayed delivery is a duplicate.
4. Correct cross-repo producer/target guidance that still promises PR-body-edit
   auto-rereview or treats `@clawsweeper review` as a Conductor rereview
   command (`openclaw-smcbd-suite` PR #38 text and ClawSweeper producer docs).
5. Keep merge, deploy, App permission expansion, and protection changes on
   their own go/no-go gates.

Live verification is blocked until those separately authorized steps exist.
Do not treat fixture PASS as a deployed review PASS.
