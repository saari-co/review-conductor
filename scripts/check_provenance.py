"""Verify the extraction ledger; edits require an explicit provenance update."""
import hashlib
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
manifest = json.loads((root / "docs/provenance.json").read_text())
for item in manifest["files"]:
    path = root / item["destination_path"]
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != item["destination_sha256"]:
        raise SystemExit(f"extraction provenance changed: {path.relative_to(root)}")
for file in [*root.glob("tools/*.py"), *root.glob("scripts/*.py"), *root.glob("tests/*.py")]:
    compile(file.read_bytes(), str(file), "exec")
print(f"extraction hashes verified ({len(manifest['files'])} files); Python compilation passed")
