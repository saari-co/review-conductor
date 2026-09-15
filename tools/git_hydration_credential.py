#!/usr/bin/env python3
"""Fixed one-use Git credential consumer. No target code or stored credentials."""

import os
import re
import stat
import sys


def main() -> int:
    if len(sys.argv) != 4:
        return 1
    descriptor, repository, operation = sys.argv[1:]
    if operation in {"store", "erase"}:
        return 0  # Never persist a value supplied back by Git.
    if operation != "get" or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        return 1
    fields = {}
    size = 0
    for line in sys.stdin:
        size += len(line)
        if size > 4096:
            return 1
        if line == "\n":
            break
        key, separator, value = line.rstrip("\n").partition("=")
        if not separator or key in fields:
            return 1
        fields[key] = value
    if (fields.get("protocol") != "https" or fields.get("host") != "github.com"
            or fields.get("path") != repository + ".git"):
        return 1
    fd = int(descriptor)
    if not stat.S_ISFIFO(os.fstat(fd).st_mode):
        return 1
    token = os.read(fd, 2049)
    os.close(fd)
    if not re.fullmatch(rb"[A-Za-z0-9_.-]{1,2048}", token):
        return 1
    # Git's private helper pipe is the intended recipient, never operator output.
    sys.stdout.buffer.write(b"username=x-access-token\npassword=" + token + b"\n\n")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(1)  # No traceback or credential-bearing input in diagnostics.
