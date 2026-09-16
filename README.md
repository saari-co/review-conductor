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
| `tools/trusted_admission.py` | Inert service-owned enrollment and policy-binding library |
| `tools/service_runtime.py`, `tools/service_entrypoint.py` | Inactive authenticated ingress, policy transport/binding and guarded worker entrypoint; not packaged or deployed |
| `tools/standalone_supervisor.py` | Source-only foreground supervisor with inherited-descriptor credential transport, parent-lifetime fail-closed drain, and identity-bound local lifecycle control; not installed or started |
| `tools/review_conductor_userland_launcher.py` | Approved 1Password-aware launcher: legacy `start` remains the Blocks 9443 consumer. `standalone preflight` only validates an external registry and bootstrap; `standalone start` forwards inherited webhook/App descriptors, launches the SMCBD supervisor, handles SIGHUP, and waits through the supervisor drain budget; `standalone health` only queries that supervisor. Source-only, uninstalled, and it does not start a tunnel |
| `contracts/target-manifest.schema.json` | Versioned repository manifest schema |
| `contracts/review-conductor/` | Unchanged historical Blocks profile fixtures plus the inactive SMCBD candidate profile; not deployment configuration |
| `tests/` | Blocks compatibility, isolated SMCBD, manifest and packaged-build coverage |
| `docs/` | Architecture, integration, migration and source provenance |

[Architecture](docs/architecture.md) · [Integration contract](docs/integration-contract.md)
· [Trusted admission](docs/trusted-admission.md) · [SMCBD pilot handoff](docs/smcbd-pilot-handoff.md) · [Migration status](docs/migration.md)
· [Proof](proof/scaffold-20260911/PROOF.md)

No open-source license is assigned by this scaffold; licensing remains an owner
choice. Do not infer redistribution permission from the source extraction.

## Governance and security

[Contributing / owners](CONTRIBUTING.md) · [Security](SECURITY.md) ·
[Bootstrap gates](docs/bootstrap.md) · [Main protection](docs/branch-protection.md)

The protection document records the live GitHub baseline; none of these source
files provide service enrollment, reviewer authority, or permission to change it.
