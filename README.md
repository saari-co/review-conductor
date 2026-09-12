# Review Conductor

Independent, deterministic review orchestration across repositories.

**Source-only / not deployed.** The extracted engine, trusted admission library,
and inactive authenticated-ingress core are present. There is no installed
service, live adapter, credential, or Gateway integration in this repo.

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
| `tools/trusted_admission.py` | Service-owned enrollment registry, approved base-policy loader and tuple/epoch policy binding (library only; not packaged, no transport) |
| `tools/service_runtime.py` | Inactive authenticated ingress and atomic exact-policy binding with injected registry/policy transports |
| `tools/service_entrypoint.py` | Service-owned registry file validation only; no executable service entrypoint |
| `contracts/target-manifest.schema.json` | Versioned repository manifest schema |
| `contracts/review-conductor/` | Unchanged historical profile fixtures, not deployment configuration |
| `tests/` | Blocks compatibility, isolated SMCBD, manifest and packaged-build coverage |
| `docs/` | Architecture, integration, migration and source provenance |

[Architecture](docs/architecture.md) · [Integration contract](docs/integration-contract.md)
· [Trusted admission](docs/trusted-admission.md) · [Migration status](docs/migration.md)
· [Proof](proof/scaffold-20260911/PROOF.md)

No open-source license is assigned by this scaffold; licensing remains an owner
choice. Do not infer redistribution permission from the source extraction.

## Governance and security

[Contributing / owners](CONTRIBUTING.md) · [Security](SECURITY.md) ·
[Bootstrap gates](docs/bootstrap.md) · [Main protection](docs/branch-protection.md)

The protection document records the live GitHub baseline; none of these source
files provide service enrollment, reviewer authority, or permission to change it.
