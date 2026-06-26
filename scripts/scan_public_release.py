#!/usr/bin/env python3
"""Scan a public CHVD release for local paths, credential-like strings, and private trees.

The scan writes a runtime report outside the immutable release_audit/ tree by default.
It detects both ordinary and JSON-escaped Windows absolute paths.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

WINDOWS_PATH = re.compile(r"(?i)(?:[A-Z]:\\\\{1,2}(?:[^\\\r\n\"']+\\\\{1,2})*[^\\\r\n\"']+)")
UNIX_PATH = re.compile(r"(?:(?:/Users/|/home/|/mnt/)[^ \r\n\"']+)")
KEY_PATTERNS = [
    re.compile(r"AIza[0-9A-Za-z_-]{20,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
]
TEXT_SUFFIXES = {".py", ".md", ".txt", ".csv", ".json", ".jsonl", ".cff", ".yml", ".yaml", ".toml", ".tex", ".bib"}

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument(
        "--out",
        default=None,
        help="Runtime report location. Defaults to <root>/results/runtime_release_scan.json.",
    )
    args = parser.parse_args()
    root = Path(args.root).resolve()
    findings: list[dict[str, str]] = []

    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel_parts = path.relative_to(root).parts
        rel = path.relative_to(root).as_posix()
        if rel == "scripts/scan_public_release.py":
            continue
        if rel.startswith("results/runtime_"):
            continue
        if "Private" in rel_parts:
            findings.append({"kind": "private_tree", "path": rel})
            continue
        if path.suffix.lower() in TEXT_SUFFIXES or path.name == ".gitignore":
            text = path.read_text(encoding="utf-8", errors="replace")
            if WINDOWS_PATH.search(text) or UNIX_PATH.search(text):
                findings.append({"kind": "local_absolute_path", "path": rel})
            if any(pattern.search(text) for pattern in KEY_PATTERNS):
                findings.append({"kind": "credential_like_pattern", "path": rel})
            placeholders = (
                "UNPUBLISHED_REPOSITORY_URL",
                "REPLACE_WITH_",
                "YOUR_ACCOUNT",
                "<replace-with",
                "[replace-with",
            )
            if any(token.lower() in text.lower() for token in placeholders):
                findings.append({"kind": "placeholder", "path": rel})

    payload = {"findings": findings, "blocker_count": len(findings)}
    output = Path(args.out).resolve() if args.out else root / "results" / "runtime_release_scan.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))
    return 1 if findings else 0

if __name__ == "__main__":
    raise SystemExit(main())
