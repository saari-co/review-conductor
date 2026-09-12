"""Source-only Git-index hygiene. Prints paths/reasons, never matched contents.

This is a bounded regression guard, not exhaustive secret detection or a trusted
policy-admission boundary. Review the exact diff against approved base policy.
"""
import json
from pathlib import Path, PurePosixPath
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
MAX_FILE_BYTES = 2 * 1024 * 1024
FORBIDDEN_DIRS = {
    'state', 'runs', 'dist', 'runtime', 'credentials', 'secrets', 'checkouts',
    'inbox', 'outbox', 'logs', '.ssh', '.aws', '.venv', '__pycache__',
}
FORBIDDEN_SUFFIXES = ('.pem', '.key', '.token', '.p12', '.pfx', '.log', '.jsonl', '.pyc')
# Deliberately high-confidence signatures; never print the matching bytes.
SECRET_MARKERS = [
    re.compile(rb'-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----'),
    re.compile(rb'\bgh[pousr]_[A-Za-z0-9]{36,}\b'),
    re.compile(rb'\bgithub_pat_[A-Za-z0-9_]{60,}\b'),
    re.compile(rb'\bAKIA[A-Z0-9]{16}\b'),
]
REQUIRED = {
    '.github/owners.json', '.github/CODEOWNERS', '.github/pull_request_template.md',
    'CONTRIBUTING.md', 'SECURITY.md', 'AGENTS.md', 'docs/bootstrap.md',
    'docs/branch-protection.md', 'docs/main-protection.proposed.json',
}


def path_violation(name, mode):
    path = PurePosixPath(name)
    if mode not in {'100644', '100755'}:
        return 'only regular source files are permitted (no symlinks/submodules)'
    if any(part.lower() in FORBIDDEN_DIRS for part in path.parts[:-1]):
        return 'prohibited runtime/credential/generated directory'
    lower = path.name.lower()
    if (lower == '.env' or lower.startswith('.env.') or
            lower.endswith(FORBIDDEN_SUFFIXES) or
            re.search(r'\.(sqlite\d*|db)(-|\.|$)', lower)):
        return 'prohibited credential/runtime file type'
    return None


def content_violation(raw):
    if len(raw) > MAX_FILE_BYTES:
        return 'oversized source file; keep runtime/build artifacts outside Git'
    if any(pattern.search(raw) for pattern in SECRET_MARKERS):
        return 'credential marker detected; contents withheld'
    try:
        raw.decode('utf-8')
    except UnicodeError:
        return 'non-UTF-8/binary material is not source-only scaffold content'
    if b'\0' in raw:
        return 'binary material is not source-only scaffold content'
    return None


def check(root):
    raw = subprocess.check_output(['git', 'ls-files', '--stage', '-z'], cwd=root)
    errors, names = [], set()
    for entry in raw.split(b'\0'):
        if not entry:
            continue
        metadata, name_bytes = entry.split(b'\t', 1)
        mode, oid, stage = metadata.decode('ascii').split()
        name = name_bytes.decode('utf-8', errors='surrogateescape')
        names.add(name)
        reason = path_violation(name, mode)
        path = root / name
        if stage != '0':
            reason = 'unresolved index conflict'
        if not reason and (any(p.is_symlink() for p in [path, *path.parents]
                                    if p != root and p.is_relative_to(root)) or not path.is_file()):
            reason = 'tracked source missing or not a regular file'
        if not reason:
            # Check the index too: an unstaged edit must not hide a staged leak.
            # Do not read known credential/runtime paths or oversized blobs.
            size = int(subprocess.check_output(['git', 'cat-file', '-s', oid], cwd=root))
            if size > MAX_FILE_BYTES:
                reason = 'oversized indexed source file'
            else:
                reason = content_violation(subprocess.check_output(
                    ['git', 'cat-file', 'blob', oid], cwd=root))
        if not reason:
            with path.open('rb') as stream:
                reason = content_violation(stream.read(MAX_FILE_BYTES + 1))
        if reason:
            errors.append(f'{json.dumps(name)}: {reason}')
    for missing in sorted(REQUIRED - names):
        errors.append(f'{json.dumps(missing)}: required governance file not tracked')
    if errors:
        return errors
    owners = json.loads((root / '.github/owners.json').read_text())
    if (set(owners) != {'schema', 'repository', 'owners'} or
            owners['schema'] != 'review-conductor.governance-owners.v1' or
            owners['repository'] != 'saari-co/review-conductor' or
            len(owners['owners']) != 2):
        return ['invalid repository governance owner map']
    logins, ids = [], []
    for owner in owners['owners']:
        if (set(owner) != {'login', 'github_user_id'} or
                not isinstance(owner['login'], str) or
                not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]{0,38}', owner['login']) or
                type(owner['github_user_id']) is not int or owner['github_user_id'] <= 0):
            return ['invalid governance owner identity']
        logins.append(owner['login'].lower())
        ids.append(owner['github_user_id'])
    if len(set(logins)) != 2 or len(set(ids)) != 2:
        return ['governance requires two distinct owner identities']
    lines = [line.strip().split() for line in
             (root / '.github/CODEOWNERS').read_text().splitlines()
             if line.strip() and not line.lstrip().startswith('#')]
    if lines != [['*', *['@' + owner['login'] for owner in owners['owners']]]]:
        return ['CODEOWNERS must cover every path with both mapped owners and no overrides']
    return []


def main():
    try:
        errors = check(ROOT)
    except (OSError, ValueError, TypeError, KeyError, subprocess.CalledProcessError):
        errors = ['repository guard could not validate source/governance; details withheld']
    if errors:
        print('\n'.join(errors))
        return 1
    print('tracked source hygiene and complete two-owner CODEOWNERS coverage passed')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
