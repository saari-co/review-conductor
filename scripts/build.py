"""Build a deterministic offline zipapp using only the Python standard library."""
import argparse
from pathlib import Path
from zipfile import ZipFile, ZipInfo, ZIP_STORED

ROOT = Path(__file__).resolve().parents[1]

def build(output):
    # Explicit allowlist: never include legacy profiles, launchers, state or secrets.
    files = {name: (ROOT / "tools" / name).read_bytes() for name in
             ("conductor_cli.py", "target_manifest.py")}
    files["__main__.py"] = b"from conductor_cli import main\nraise SystemExit(main())\n"
    output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output, "w", compression=ZIP_STORED) as archive:
        for name, content in sorted(files.items()):
            item = ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            item.create_system = 3
            item.external_attr = 0o100644 << 16
            archive.writestr(item, content)
    return output

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "dist/review-conductor.pyz")
    print(build(parser.parse_args().output))
