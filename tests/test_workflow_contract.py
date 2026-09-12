"""Exact event checkout/diff contracts, exercised with real temporary Git commits."""
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/check_whitespace.py"


def workflow_contract(text):
    required = [
        'EXPECTED_BASE: ${{ github.event.pull_request.base.sha || github.event.before }}',
        'EXPECTED_HEAD: ${{ github.event.pull_request.head.sha || github.sha }}',
        'ref: ${{ env.EXPECTED_HEAD }}', 'persist-credentials: false', 'fetch-depth: 0',
        'run: test "$(git rev-parse HEAD)" = "$EXPECTED_HEAD"',
        'run: python3 scripts/check_whitespace.py "$EXPECTED_BASE" "$EXPECTED_HEAD"',
        'needs: [test]', 'if: ${{ always() }}',
        'TEST_RESULT: ${{ needs.test.result }}', 'run: test "$TEST_RESULT" = success',
        'contents: read', 'run: make check', 'run: make build',
    ]
    for item in required:
        if item not in text:
            raise AssertionError(f"missing workflow contract: {item}")
    uses = re.findall(r'uses: (\S+)', text)
    if len(uses) != 2 or not all(re.fullmatch(r'actions/[a-z-]+@[a-f0-9]{40}', x) for x in uses):
        raise AssertionError("actions must remain immutable-pinned")
    if any(x in text for x in ['pull_request_target:', 'secrets.', 'contents: write', 'continue-on-error:']):
        raise AssertionError("unsafe workflow authority/failure bypass")


class WorkflowContractTests(unittest.TestCase):
    def test_real_workflow_and_precise_contract_mutants(self):
        text = (ROOT / '.github/workflows/ci.yml').read_text()
        workflow_contract(text)
        for old, new in [
            ('fetch-depth: 0', 'fetch-depth: 1'),
            ('github.event.pull_request.base.sha', 'github.event.pull_request.base.ref'),
            ('github.event.pull_request.head.sha', 'github.sha'),
            ('python3 scripts/check_whitespace.py "$EXPECTED_BASE" "$EXPECTED_HEAD"', 'git diff --check'),
            ('if: ${{ always() }}', 'if: ${{ success() }}'),
            ('test "$TEST_RESULT" = success', 'true'),
            ('persist-credentials: false', 'persist-credentials: true'),
            ('actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683', 'actions/checkout@v4'),
        ]:
            with self.subTest(mutant=old), self.assertRaises(AssertionError):
                workflow_contract(text.replace(old, new))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.git('init', '-q')
        self.git('config', 'user.name', 'Synthetic Test')
        self.git('config', 'user.email', 'fixture@example.invalid')
        self.base = self.commit('clean\n')

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.root), *args], text=True).strip()

    def commit(self, contents):
        (self.root / 'fixture.txt').write_text(contents)
        self.git('add', 'fixture.txt')
        self.git('-c', 'commit.gpgsign=false', 'commit', '-qm', 'synthetic contract fixture')
        return self.git('rev-parse', 'HEAD')

    def check(self, base, head, root=None):
        return subprocess.run([sys.executable, str(SCRIPT), base, head], cwd=root or self.root,
                              capture_output=True, text=True, timeout=10)

    def test_committed_whitespace_mutant_fails_in_clean_checkout(self):
        head = self.commit('clean\ncommitted whitespace mutant' + ' \n')
        self.assertEqual(self.git('status', '--porcelain'), '')
        # The old check is green despite the *committed* defect.
        self.assertEqual(self.git('diff', '--check'), '')
        result = self.check(self.base, head)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('trailing whitespace', result.stdout)
        fixed = self.commit('clean\ncommitted whitespace mutant\n')
        self.assertEqual(self.check(self.base, fixed).returncode, 0)

    def test_exact_base_not_merge_base_or_only_last_commit(self):
        # Divergent base fixes ancestor whitespace. Head still has it; exact
        # two-tree comparison must reject, although merge-base diff is clean.
        ancestor = self.commit('ancestor' + ' \n')
        base = self.commit('ancestor\n')
        self.git('checkout', '-q', '--detach', ancestor)
        (self.root / 'other.txt').write_text('clean\n')
        self.git('add', 'other.txt')
        self.git('-c', 'commit.gpgsign=false', 'commit', '-qm', 'divergent head')
        head = self.git('rev-parse', 'HEAD')
        self.assertEqual(self.git('diff', '--check', base + '...' + head), '')
        self.assertNotEqual(self.check(base, head).returncode, 0)

    def test_missing_base_shallow_checkout_fails_then_full_history_passes(self):
        head = self.commit('clean change\n')
        shallow = self.root / 'shallow'
        subprocess.run(['git', 'clone', '-q', '--depth=1', self.root.as_uri(), str(shallow)], check=True)
        self.assertNotEqual(self.check(self.base, head, shallow).returncode, 0)
        subprocess.run(['git', '-C', str(shallow), 'fetch', '-q', '--unshallow'], check=True)
        self.assertEqual(self.check(self.base, head, shallow).returncode, 0)

    def test_missing_invalid_noncommit_or_wrong_head_fails_closed(self):
        head = self.commit('clean new\n')
        blob = self.git('rev-parse', 'HEAD:fixture.txt')
        for base, candidate in [(self.base, self.base), ('f' * 40, head), (blob, head),
                                ('main', head), ('0' * 40, head), (self.base, '--help')]:
            with self.subTest(base=base, head=candidate):
                self.assertNotEqual(self.check(base, candidate).returncode, 0)


if __name__ == '__main__':
    unittest.main()
