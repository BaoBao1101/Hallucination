"""Project path helpers for CHVD scripts.

Use these helpers instead of hard-coded absolute Windows paths.
"""
from __future__ import annotations

from pathlib import Path

MAIN_STUDY_RELATIVE = Path("Study_Gemini3.1Flash-Lite/P1")


def find_repo_root(start: str | Path | None = None) -> Path:
    current = Path(start or __file__).resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if (candidate / "code").is_dir() and (candidate / MAIN_STUDY_RELATIVE).is_dir():
            return candidate
    raise FileNotFoundError("Could not locate a CHVD repository root.")


def main_study_root(start: str | Path | None = None) -> Path:
    return find_repo_root(start) / MAIN_STUDY_RELATIVE
