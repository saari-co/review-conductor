# Review Conductor

This repository owns deterministic review orchestration, not a conversational
agent, Gateway, CI executor, source-repair worker, or merge service.

- Read docs/architecture.md and docs/integration-contract.md before changes.
- One mutation owner per isolated branch/worktree. Commit work; never self-merge.
- Preserve repository/PR/base/head/epoch binding, HMAC/replay protection,
  stale-result rejection, action claims, bounded repairs, and human-only merge.
- Repository manifests describe requirements; they cannot grant authority.
  Never execute commands or load credentials from a reviewed repository.
- Keep credentials, live databases, checkouts and proof stores outside Git.
- Tests and builds must work without x-api, OpenClaw, Smoky, or live credentials.
- Run `make check` and `make build` for relevant source changes. Preserve Blocks
  regression coverage; profile fixture PASS is not a deployed review PASS.
- Changes to deployments, credentials, protection, merges, and adjudication
  require their own explicit authorization. No activation follows from a PR.
- Keep dated proof in proof/, stable contracts in docs/, generated output in runs/.
