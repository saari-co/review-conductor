"""Security contract and independent packaged-artifact behavior."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from target_manifest import validate_manifest

class ScaffoldTests(unittest.TestCase):
    def setUp(self):
        self.value = json.loads((ROOT / "examples/smcbd.review-conductor.json").read_text())

    def test_repository_requirements_have_no_authority_fields(self):
        for field, value in {
            "credentials": "borrow-blocks", "reviewers": ["attacker"],
            "command": "arbitrary-command", "installation_id": 42,
            "state_root": "/another-repository", "enabled": True,
            "policy_sha": "unapproved", "endpoint": "https://example.invalid"
        }.items():
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_manifest(json.dumps({**self.value, field: value}).encode())

    def test_weakened_or_ambiguous_policy_fails_closed(self):
        candidates = []
        for key, value in [("quiet_seconds", 0), ("quiet_seconds", True),
                           ("scope", "P0"), ("rails", ["clawsweeper", "openclaw"]),
                           ("rails", ["openclaw"])]:
            item = copy.deepcopy(self.value);item["review"][key] = value;candidates.append(item)
        item = copy.deepcopy(self.value);item["merge_policy"] = "automatic";candidates.append(item)
        item = copy.deepcopy(self.value);item["ci"]["workflow_path"] = "../../run.sh";candidates.append(item)
        item = copy.deepcopy(self.value);item["ci"]["command"] = "echo pass";candidates.append(item)
        for item in candidates:
            with self.subTest(item=item), self.assertRaises(ValueError):
                validate_manifest(json.dumps(item).encode())
        for raw in [b'{"schema":1,"schema":2}', b' ' * 16385, b'\xff', b'[' * 2000]:
            with self.subTest(raw=raw[:30]), self.assertRaises(ValueError):
                validate_manifest(raw)

    def test_examples_and_nonactivating_cli(self):
        for path in sorted((ROOT / "examples").glob("*.json")):
            self.assertEqual(validate_manifest(path.read_bytes())["merge_policy"], "human_only")
            result = subprocess.run([sys.executable, str(ROOT / "bin/review-conductor"),
                                     "validate-manifest", str(path)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(json.loads(result.stdout)["activation_supported"])

    def test_packaged_build_is_reproducible_and_independent(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            outputs = [base / "one.pyz", base / "two.pyz"]
            for out in outputs:
                subprocess.run([sys.executable, str(ROOT / "scripts/build.py"), "--output", str(out)],
                               check=True, capture_output=True)
            self.assertEqual(outputs[0].read_bytes(), outputs[1].read_bytes())
            with zipfile.ZipFile(outputs[0]) as archive:
                self.assertEqual(set(archive.namelist()), {"__main__.py", "target_manifest.py", "conductor_cli.py"})
            manifest = base / ".review-conductor.json"
            manifest.write_text(json.dumps(self.value))
            env = {"PATH":os.defpath, "HOME":str(base), "PYTHONNOUSERSITE":"1"}
            result = subprocess.run([sys.executable, "-I", str(outputs[0]), "validate-manifest", str(manifest)],
                                    cwd=base, env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(json.loads(result.stdout)["activation_supported"])
            for command in ["start", "bootstrap", "dispatch", "merge"]:
                result = subprocess.run([sys.executable, "-I", str(outputs[0]), command],
                                        cwd=base, env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 2)
            self.assertEqual(set(base.iterdir()), {*outputs, manifest})

if __name__ == "__main__":
    unittest.main()
