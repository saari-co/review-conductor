"""Negative tests for tracked source hygiene and owner coverage."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import check_repository as guard


class RepositoryGuardTests(unittest.TestCase):
    def test_runtime_paths_and_indirections_rejected(self):
        for name in ['.env', '.env.production', 'test/secret.pem', 'dump.sqlite-wal',
                     'state/a.json', 'a/credentials/export.txt', 'review.db-shm',
                     'logs/output.txt', 'outbox/event.json', 'a/auth.jsonl']:
            with self.subTest(name=name):
                self.assertIsNotNone(guard.path_violation(name, '100644'))
        for mode in ['120000', '160000']:
            self.assertIsNotNone(guard.path_violation('innocent.txt', mode))
        self.assertIsNone(guard.path_violation('proof/checks/PROOF.md', '100644'))

    def test_marker_results_never_contain_values(self):
        for raw in [(b'-----BEGIN ' + b'PRIVATE KEY-----'), b'ghp_' + b'A' * 36,
                    b'github_pat_' + b'a' * 82, b'AKIA' + b'Z' * 16]:
            reason = guard.content_violation(raw)
            self.assertIn('withheld', reason)
            self.assertNotIn(raw.decode(), reason)
        self.assertIsNone(guard.content_violation(b'fixture-webhook-secret-not-for-live-use'))
        self.assertIsNotNone(guard.content_violation(b'\0binary'))
        self.assertIsNotNone(guard.content_violation(b'x' * (guard.MAX_FILE_BYTES + 1)))

    def test_force_added_ignored_material_and_owner_override_fail(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            subprocess.run(['git', 'init', '-q', str(root)], check=True)
            for name in guard.REQUIRED:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes((ROOT / name).read_bytes())
            subprocess.run(['git', '-C', str(root), 'add', '.'], check=True)
            self.assertEqual(guard.check(root), [])
            (root / '.gitignore').write_text('.env\n')
            (root / '.env').write_text('SYNTHETIC_FIXTURE_ONLY=true\n')
            subprocess.run(['git', '-C', str(root), 'add', '-f', '.env'], check=True)
            self.assertTrue(any('prohibited' in e for e in guard.check(root)))
            subprocess.run(['git', '-C', str(root), 'rm', '--cached', '-q', '.env'], check=True)
            with (root / '.github/CODEOWNERS').open('a') as stream:
                stream.write('/tools/ @untrusted\n')
            self.assertTrue(any('CODEOWNERS' in e for e in guard.check(root)))

    def test_unstaged_edit_cannot_hide_prohibited_index_content(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            subprocess.run(['git', 'init', '-q', str(root)], check=True)
            for name in guard.REQUIRED:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes((ROOT / name).read_bytes())
            sample = root / 'sample.txt'
            sample.write_bytes(b'ghp_' + b'A' * 36)
            subprocess.run(['git', '-C', str(root), 'add', '.'], check=True)
            sample.write_text('harmless unstaged replacement')
            self.assertTrue(any('credential marker' in e for e in guard.check(root)))

    def test_duplicate_owner_id_is_not_two_owners(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            subprocess.run(['git', 'init', '-q', str(root)], check=True)
            for name in guard.REQUIRED:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes((ROOT / name).read_bytes())
            owners_path = root / '.github/owners.json'
            owners = json.loads(owners_path.read_text())
            owners['owners'][1]['github_user_id'] = owners['owners'][0]['github_user_id']
            owners_path.write_text(json.dumps(owners))
            subprocess.run(['git', '-C', str(root), 'add', '.'], check=True)
            self.assertTrue(any('distinct' in e for e in guard.check(root)))


if __name__ == '__main__':
    unittest.main()
