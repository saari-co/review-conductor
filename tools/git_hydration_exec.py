#!/usr/bin/env python3
"""Fixed Git hydration exec boundary; limits are inherited by Git children.

Not a host quota: FSIZE is per file, DATA/AS are per process on Linux. macOS does
not support these memory limits consistently, so no macOS memory quota is claimed. No diagnostic/secret output.
"""
import os
import resource
import sys

FILE_BYTES = 256 * 1024 * 1024
MEMORY_BYTES = 2 * 1024 * 1024 * 1024


def limit(kind: int, maximum: int) -> None:
    soft, hard = resource.getrlimit(kind)
    bounds = [maximum, *(n for n in (soft, hard) if n != resource.RLIM_INFINITY)]
    ceiling = min(bounds)
    resource.setrlimit(kind, (ceiling, ceiling))


def main() -> int:
    if len(sys.argv) < 3 or sys.argv[1] != "/usr/bin/git":
        return 78
    try:
        limit(resource.RLIMIT_CORE, 0)
        limit(resource.RLIMIT_FSIZE, FILE_BYTES)
        limit(resource.RLIMIT_CPU, 180)
        if sys.platform.startswith("linux"):
            limit(resource.RLIMIT_DATA, MEMORY_BYTES)
            limit(resource.RLIMIT_AS, MEMORY_BYTES)
        os.execv(sys.argv[1], sys.argv[1:])
    except (OSError, ValueError):
        return 78
    return 78


if __name__ == "__main__":
    sys.exit(main())
