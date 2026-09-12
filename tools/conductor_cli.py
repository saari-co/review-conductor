"""Offline standalone scaffold. No deployment or credential operations."""
import argparse
import json
from pathlib import Path
from target_manifest import MAX_BYTES, validate_manifest

VERSION = "0.1.0.dev0"

def main(argv=None):
    parser = argparse.ArgumentParser(description="Review Conductor offline scaffold")
    parser.add_argument("--version", action="version", version=VERSION)
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("validate-manifest", help="Validate repository requirements; does not enroll or activate")
    command.add_argument("path", type=Path)
    args = parser.parse_args(argv)
    try:
        with args.path.open("rb") as stream:
            value = validate_manifest(stream.read(MAX_BYTES + 1))
    except (OSError, ValueError) as exc:
        print(json.dumps({"valid": False, "error": str(exc), "activation_supported": False}))
        return 2
    print(json.dumps({"valid": True, "repository": value["repository"], "schema": value["schema"], "activation_supported": False}))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
