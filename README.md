# Review Conductor

Independent, deterministic review orchestration across repositories.

**Scaffold / not deployed.** The extracted engine and its offline regressions
are present. There is no installed service or Gateway integration in this repo.

## Boundary

- This repo owns the engine, adapter contracts, tests, build and releases.
- Each target repo owns `.review-conductor.json`: its CI and review requirements.
- The service owns trusted enrollment, credentials and isolated runtime state.
- Gateway and agents are clients, never owners of the review state machine.
- GitHub Actions owns CI; the conductor App owns the two external rail checks.
  Only a human can authorize merge.

## Local development

Python 3.11+ on macOS or Linux; no third-party Python or Node dependencies.

```sh
make check
make build
python3 dist/review-conductor.pyz --help
python3 dist/review-conductor.pyz validate-manifest examples/smcbd.review-conductor.json
```

The zipapp is an offline manifest-validation scaffold, not a deployment artifact
for the legacy pilot. It intentionally exposes no start, bootstrap, dispatch,
merge, or credential commands.

## Layout

| Path | Owns |
| --- | --- |
| `tools/review_conductor*.py` | Extracted engine and compatibility adapters |
| `tools/conductor_cli.py`, `tools/target_manifest.py` | Independent offline CLI and target contract |
| `contracts/target-manifest.schema.json` | Versioned repository manifest schema |
| `contracts/review-conductor/` | Unchanged historical profile fixtures, not deployment configuration |
| `tests/` | Blocks compatibility, isolated SMCBD, manifest and packaged-build coverage |
| `docs/` | Architecture, integration, migration and source provenance |

[Architecture](docs/architecture.md) · [Integration contract](docs/integration-contract.md)
· [Migration status](docs/migration.md) · [Proof](proof/scaffold-20260911/PROOF.md)

No open-source license is assigned by this scaffold; licensing remains an owner
choice. Do not infer redistribution permission from the source extraction.

## Governance and security

[Contributing / owners](CONTRIBUTING.md) · [Security](SECURITY.md) ·
[Bootstrap gates](docs/bootstrap.md) · [Proposed protection](docs/branch-protection.md)

These are source-review rules, not live enrollment or applied GitHub protection.
