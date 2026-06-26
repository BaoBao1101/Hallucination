#!/usr/bin/env python3
"""
Create a reproducibility manifest with SHA256 hashes.

By default it skips likely secrets/private files. Use --include-private only
for a local private archive, never for a public release.
"""
from __future__ import annotations

import argparse
import csv
import os
from pathlib import Path

# Allow direct execution from a cloned repository without requiring PYTHONPATH.
import sys
_CODE_ROOT = Path(__file__).resolve().parents[1]
if str(_CODE_ROOT) not in sys.path:
    sys.path.insert(0, str(_CODE_ROOT))

import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from chvd_tools.io_utils import sha256_file


PRIVATE_MARKERS = (
    "key_private",
    "blind_key",
    "pairwise_key_private",
    "directional_key_private",
    ".env",
    "session_links",
    "credentials",
    "secret",
    "api_key",
)


def is_private(path: Path) -> bool:
    low = str(path).lower()
    return any(marker in low for marker in PRIVATE_MARKERS)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, help="Directory to hash recursively.")
    parser.add_argument("--out", required=True, help="CSV manifest path.")
    parser.add_argument("--include-private", action="store_true")
    args = parser.parse_args()

    root = Path(args.root).expanduser().resolve()
    out = Path(args.out).expanduser().resolve()
    if not root.exists():
        raise FileNotFoundError(root)

    rows = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        if path.resolve() == out.resolve():
            continue
        if not args.include_private and is_private(path.relative_to(root)):
            continue
        rows.append({
            "relative_path": path.relative_to(root).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        })

    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["relative_path", "bytes", "sha256"])
        writer.writeheader()
        writer.writerows(rows)

    print(f"[OK] Manifest rows: {len(rows)}")
    print(f"[OK] Wrote: {out}")


if __name__ == "__main__":
    main()
