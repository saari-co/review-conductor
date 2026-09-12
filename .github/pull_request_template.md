## Scope and trust boundary

Describe changes, approved base/head, and anything affecting policy, ownership,
CI, authentication, isolation, state, evidence or packaged contents.

## Exact-head evidence

- Base / head:
- Local checks and sanitized proof:
- Hosted check conclusions and URLs:
- External reviews (missing/skipped is not PASS):
- Remaining findings / blockers:

## Boundaries

- [ ] No secrets, auth-bearing logs, runtime stores or live configuration committed.
- [ ] Blocks regression coverage preserved; fixture PASS is not live clearance.
- [ ] No deployment, enrollment, credentials, settings, readiness, merge or adjudication change implied.
- [ ] Governance changes evaluated against approved base, not self-authorized PR policy.
- [ ] Required human owner review/acknowledgements are recorded externally at this exact head, or explicitly pending.

For PR #1, keep draft: bootstrap is a separate human decision under
`docs/bootstrap.md`. These boxes are author disclosures, not approvals.
