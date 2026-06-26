#!/usr/bin/env python3
"""Run all offline public release checks without mutating frozen manifest artifacts."""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

def run(root: Path, name: str) -> int:
    command = [sys.executable, str(root / "scripts" / name), "--root", str(root)]
    print("+", " ".join(command))
    return subprocess.run(command, cwd=root, check=False).returncode

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    args = parser.parse_args()
    root = Path(args.root).resolve()

    # The release must be intact before and after runtime checks.
    sequence = [
        "verify_release_integrity.py",
        "validate_public_release.py",
        "reproduce_public_results.py",
        "scan_public_release.py",
        "verify_release_integrity.py",
    ]
    failures = sum(run(root, name) != 0 for name in sequence)
    return 1 if failures else 0

if __name__ == "__main__":
    raise SystemExit(main())
