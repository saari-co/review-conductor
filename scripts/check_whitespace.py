"""Check committed whitespace against the exact event base, never a merge base."""
import re
import subprocess
import sys


def check(base: str, head: str) -> int:
    for revision in (base, head):
        if re.fullmatch(r"[0-9a-f]{40}", revision) is None or revision == "0" * 40:
            raise ValueError("nonzero full base/head commit SHAs are required")
        # Full-history checkout is required by CI; prove both objects are commits.
        kind = subprocess.check_output(["git", "cat-file", "-t", revision], text=True).strip()
        if kind != "commit":
            raise ValueError("base/head object is not a commit")
    actual = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    if actual != head:
        raise ValueError("checkout does not match the exact event head")
    return subprocess.run([
        "git", "-c", "core.whitespace=blank-at-eol,blank-at-eof,space-before-tab",
        "diff", "--check", base, head, "--",
    ], check=False).returncode


if __name__ == "__main__":
    try:
        if len(sys.argv) != 3:
            raise ValueError("usage: check_whitespace.py BASE_SHA HEAD_SHA")
        raise SystemExit(check(*sys.argv[1:]))
    except (ValueError, subprocess.CalledProcessError) as exc:
        print(f"exact-commit whitespace validation failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
