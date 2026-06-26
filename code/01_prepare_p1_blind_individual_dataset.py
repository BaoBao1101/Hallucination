#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CHVD P1 — Step 1: Build a blinded individual-evaluation dataset.

Expected input tree
-------------------
outputs_P1/
  M0_<domain>/  one JSON with top-level {"ideas": [...]}
  M1_<domain>/  one JSON with top-level {"ideas": [...]}
  M3_<domain>/  one JSON with top-level {"evaluations": [...]}

The script recursively discovers the JSON payload inside each M0_, M1_, and M3_
folder, validates P1 coverage, creates private blind mappings, and splits public,
anonymous candidate cards into batches. M1/M3 siblings are kept out of the same
individual-judge batch whenever possible.

IMPORTANT:
- blind_key_private.csv is private. Never send it to any LLM judge.
- Do not rebuild this folder after score collection has begun.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import re
import shutil
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

METHODS = {
    "M0": "M0_DIRECT_LOW_TEMP",
    "M1": "M1_RAW_HIGH_TEMP",
    "M3": "M3_CHVD_DISTILLED",
}
REQUIRED_CARD_FIELDS = (
    "title",
    "target_user",
    "problem_or_desire",
    "core_concept",
    "proposed_approach",
    "prototype_scope",
)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"Cannot read JSON: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"Top-level JSON must be an object: {path}")
    return value


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "; ".join(clean_text(x) for x in value if clean_text(x))
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return re.sub(r"\s+", " ", str(value)).strip()


def first_nonempty(item: Dict[str, Any], *keys: str, default: str = "") -> str:
    for key in keys:
        value = clean_text(item.get(key))
        if value:
            return value
    return default


def norm_domain(value: str) -> str:
    value = clean_text(value).lower()
    return re.sub(r"[^a-z0-9]+", "", value)


def clip(value: str, max_chars: int) -> str:
    value = clean_text(value)
    if len(value) <= max_chars:
        return value
    return value[: max_chars - 1].rstrip() + "…"


def condition_and_domain_from_path(json_path: Path, root: Path) -> Tuple[str, str] | None:
    try:
        relative_parts = json_path.relative_to(root).parts[:-1]
    except ValueError:
        return None

    for folder in reversed(relative_parts):
        match = re.fullmatch(r"(M0|M1|M3)_(.+)", folder, flags=re.IGNORECASE)
        if match:
            return match.group(1).upper(), match.group(2).strip()
    return None


def payload_matches_condition(document: Dict[str, Any], condition: str) -> bool:
    if condition in {"M0", "M1"}:
        return isinstance(document.get("ideas"), list)
    return isinstance(document.get("evaluations"), list)


def discover_payload_files(root: Path) -> Dict[Tuple[str, str], Path]:
    discovered: Dict[Tuple[str, str], Path] = {}
    ignored: List[Path] = []

    for path in sorted(root.rglob("*.json")):
        info = condition_and_domain_from_path(path, root)
        if info is None:
            ignored.append(path)
            continue
        condition, domain = info
        try:
            document = load_json(path)
        except Exception:
            ignored.append(path)
            continue
        if not payload_matches_condition(document, condition):
            ignored.append(path)
            continue

        key = (condition, norm_domain(domain))
        if key in discovered:
            raise ValueError(
                "Multiple payload JSON files found for the same condition/domai[LOCAL_PATH_REDACTED]"
                f"  existing: {discovered[key]}\n"
                f"  duplicate: {path}\n"
                "Keep only one generated payload JSON in each M0_/M1_/M3_ folder."
            )
        discovered[key] = path

    if not discovered:
        raise FileNotFoundError(
            f"No valid P1 payload files found below {root}. "
            "Expected M0_<domain>/ideas*.json, M1_<domain>/ideas*.json, "
            "and M3_<domain>/ideas*.json."
        )
    return discovered


def raw_items(document: Dict[str, Any], source: Path) -> List[Dict[str, Any]]:
    ideas = document.get("ideas")
    if not isinstance(ideas, list) or not ideas:
        raise ValueError(f"Missing non-empty top-level 'ideas' list: {source}")
    if not all(isinstance(item, dict) for item in ideas):
        raise ValueError(f"Every item in 'ideas' must be an object: {source}")
    return ideas


def m3_items(document: Dict[str, Any], source: Path) -> List[Dict[str, Any]]:
    evaluations = document.get("evaluations")
    if not isinstance(evaluations, list) or not evaluations:
        raise ValueError(f"Missing non-empty top-level 'evaluations' list: {source}")
    if not all(isinstance(item, dict) for item in evaluations):
        raise ValueError(f"Every item in 'evaluations' must be an object: {source}")
    return evaluations


def card_from_raw(
    item: Dict[str, Any],
    *,
    method: str,
    domain: str,
    source_file: Path,
    max_chars: int,
) -> Dict[str, Any]:
    idea_id = first_nonempty(item, "idea_id", "source_idea_id", "candidate_id")
    if not idea_id:
        raise ValueError(f"Raw item lacks idea_id in {source_file}")

    return {
        "title": clip(first_nonempty(item, "title", "idea_name"), max_chars),
        "target_user": clip(first_nonempty(item, "target_user"), max_chars),
        "problem_or_desire": clip(
            first_nonempty(item, "problem_or_desire", "hidden_pain_or_desire"),
            max_chars,
        ),
        "core_concept": clip(
            first_nonempty(item, "core_concept", "creative_leap", "one_sentence_pitch"),
            max_chars,
        ),
        "proposed_approach": clip(
            first_nonempty(item, "proposed_approach", "proposed_solution"),
            max_chars,
        ),
        "prototype_scope": clip(
            first_nonempty(item, "prototype_scope", "mvp_seed", "first_7_day_prototype"),
            max_chars,
        ),
        "_method": method,
        "_domain": domain,
        "_idea_id": idea_id,
        "_pair_key": "" if method == METHODS["M0"] else f"P1::{norm_domain(domain)}::{idea_id}",
        "_source_file": str(source_file.resolve()),
    }


def card_from_m3(
    evaluation: Dict[str, Any],
    *,
    raw_m1_parent: Dict[str, Any],
    domain: str,
    source_file: Path,
    max_chars: int,
) -> Dict[str, Any]:
    idea_id = first_nonempty(evaluation, "idea_id", "source_idea_id")
    if not idea_id:
        raise ValueError(f"M3 evaluation lacks idea_id in {source_file}")

    mvp = first_nonempty(evaluation, "practical_mvp", "prototype_scope")
    validation = first_nonempty(evaluation, "mvp_7_to_30_day_plan", "validation_needed_later")
    scope = " | ".join(x for x in [mvp, validation] if x)

    return {
        "title": clip(first_nonempty(raw_m1_parent, "title", "idea_name"), max_chars),
        "target_user": clip(first_nonempty(raw_m1_parent, "target_user"), max_chars),
        "problem_or_desire": clip(
            first_nonempty(raw_m1_parent, "problem_or_desire", "hidden_pain_or_desire"),
            max_chars,
        ),
        "core_concept": clip(
            first_nonempty(
                evaluation,
                "creative_core_to_preserve",
                default=first_nonempty(raw_m1_parent, "core_concept", "creative_leap"),
            ),
            max_chars,
        ),
        "proposed_approach": clip(
            first_nonempty(evaluation, "realistic_reframing", "practical_mvp"),
            max_chars,
        ),
        "prototype_scope": clip(scope, max_chars),
        "_method": METHODS["M3"],
        "_domain": domain,
        "_idea_id": idea_id,
        "_pair_key": f"P1::{norm_domain(domain)}::{idea_id}",
        "_source_file": str(source_file.resolve()),
    }


def public_card(card: Dict[str, Any]) -> Dict[str, str]:
    output = {"candidate_id": card["_blind_id"]}
    for field in REQUIRED_CARD_FIELDS:
        output[field] = card[field]
    return output


def build_batches(cards: List[Dict[str, Any]], batch_size: int, rng: random.Random) -> List[List[Dict[str, Any]]]:
    """Greedy placement: avoid placing M1/M3 siblings in the same batch."""
    number_of_batches = math.ceil(len(cards) / batch_size)
    batches: List[List[Dict[str, Any]]] = [[] for _ in range(number_of_batches)]
    pair_keys_in_batch: List[set[str]] = [set() for _ in range(number_of_batches)]

    ordered = list(cards)
    rng.shuffle(ordered)
    # Place paired cards before M0 singleton cards to make separation easier.
    ordered.sort(key=lambda x: (x["_pair_key"] == "", rng.random()))

    for card in ordered:
        pair_key = card["_pair_key"]
        eligible = [
            idx for idx, batch in enumerate(batches)
            if len(batch) < batch_size and (not pair_key or pair_key not in pair_keys_in_batch[idx])
        ]
        if not eligible:
            raise RuntimeError(
                "Unable to separate M1/M3 siblings across batches. "
                "Use a smaller --batch-size or inspect duplicate pair keys."
            )
        min_size = min(len(batches[idx]) for idx in eligible)
        candidates = [idx for idx in eligible if len(batches[idx]) == min_size]
        selected = rng.choice(candidates)
        batches[selected].append(card)
        if pair_key:
            pair_keys_in_batch[selected].add(pair_key)

    for batch in batches:
        rng.shuffle(batch)
    return [batch for batch in batches if batch]


def write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def verify_coverage(
    discovered: Dict[Tuple[str, str], Path],
    *,
    expected_domains: int,
) -> List[str]:
    by_condition: Dict[str, set[str]] = {condition: set() for condition in METHODS}
    for condition, domain_key in discovered:
        by_condition[condition].add(domain_key)

    union_domains = set().union(*by_condition.values())
    for condition, domains in by_condition.items():
        missing = union_domains - domains
        extra = domains - union_domains
        if missing or extra:
            raise ValueError(f"Coverage mismatch for {condition}: missing={sorted(missing)}, extra={sorted(extra)}")

    if expected_domains > 0 and len(union_domains) != expected_domains:
        raise ValueError(
            f"Expected {expected_domains} domains, discovered {len(union_domains)}: {sorted(union_domains)}"
        )
    return sorted(union_domains)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build blinded P1 individual-evaluation batches from outputs_P1."
    )
    parser.add_argument("--input-root", required=True, help="Root folder containing M0_*, M1_*, M3_* folders.")
    parser.add_argument("--out-dir", required=True, help="New output folder for private mapping and public batches.")
    parser.add_argument("--seed", type=int, default=20260622)
    parser.add_argument("--batch-size", type=int, default=7, help="Recommended: 7 for 105 candidates = 15 Gemini calls.")
    parser.add_argument("--ideas-per-condition-domain", type=int, default=5)
    parser.add_argument("--expected-domains", type=int, default=7, help="Set 0 to disable the count check.")
    parser.add_argument("--max-chars", type=int, default=460)
    parser.add_argument("--rebuild", action="store_true", help="DELETE and recreate --out-dir. Do not use after judging begins.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    input_root = Path(args.input_root).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    if not input_root.is_dir():
        raise FileNotFoundError(f"--input-root does not exist: {input_root}")
    if args.batch_size < 2:
        raise ValueError("--batch-size must be >= 2")
    if args.ideas_per_condition_domain < 1:
        raise ValueError("--ideas-per-condition-domain must be >= 1")

    if out_dir.exists() and any(out_dir.iterdir()):
        if not args.rebuild:
            raise FileExistsError(
                f"Output directory is not empty: {out_dir}\n"
                "Refusing to reshuffle a potentially scored dataset. Use a new --out-dir, "
                "or use --rebuild only before any evaluation has started."
            )
        shutil.rmtree(out_dir)

    discovered = discover_payload_files(input_root)
    domains = verify_coverage(discovered, expected_domains=args.expected_domains)

    if args.dry_run:
        print("Discovered valid P1 payload files:")
        for (condition, domain_key), path in sorted(discovered.items()):
            print(f"  {condition:>2} | {domain_key:<25} | {path}")
        print(f"Domains: {len(domains)}; expected candidates: {len(domains) * 3 * args.ideas_per_condition_domain}")
        return

    out_dir.mkdir(parents=True, exist_ok=False)
    batch_dir = out_dir / "batches"
    batch_dir.mkdir()

    raw_m1_lookup: Dict[Tuple[str, str], Dict[str, Any]] = {}
    all_cards: List[Dict[str, Any]] = []
    input_manifest_rows: List[Dict[str, str]] = []

    # M0 and M1 first, so M3 can always resolve its M1 parent.
    for condition in ("M0", "M1"):
        for domain_key in domains:
            source = discovered[(condition, domain_key)]
            document = load_json(source)
            domain = condition_and_domain_from_path(source, input_root)[1]
            items = raw_items(document, source)
            if len(items) != args.ideas_per_condition_domain:
                raise ValueError(
                    f"{source} contains {len(items)} ideas; expected {args.ideas_per_condition_domain}."
                )

            for item in items:
                card = card_from_raw(
                    item,
                    method=METHODS[condition],
                    domain=domain,
                    source_file=source,
                    max_chars=args.max_chars,
                )
                if condition == "M1":
                    key = (norm_domain(domain), card["_idea_id"])
                    if key in raw_m1_lookup:
                        raise ValueError(f"Duplicate M1 parent key: {key}")
                    raw_m1_lookup[key] = item
                all_cards.append(card)

            input_manifest_rows.append({
                "condition": condition,
                "domain": domain,
                "path": str(source.resolve()),
                "sha256": sha256_file(source),
                "payload_count": str(len(items)),
            })

    # M3 must map exactly to the M1 idea ids in the same domain.
    for domain_key in domains:
        source = discovered[("M3", domain_key)]
        document = load_json(source)
        domain = condition_and_domain_from_path(source, input_root)[1]
        items = m3_items(document, source)
        if len(items) != args.ideas_per_condition_domain:
            raise ValueError(
                f"{source} contains {len(items)} M3 evaluations; expected {args.ideas_per_condition_domain}."
            )
        seen_m3_ids = set()
        for evaluation in items:
            idea_id = first_nonempty(evaluation, "idea_id", "source_idea_id")
            key = (norm_domain(domain), idea_id)
            if not idea_id or key not in raw_m1_lookup:
                raise ValueError(
                    f"M3 record cannot be matched to M1 parent: domain={domain!r}, idea_id={idea_id!r}"
                )
            if idea_id in seen_m3_ids:
                raise ValueError(f"Duplicate M3 idea_id in domain {domain}: {idea_id}")
            seen_m3_ids.add(idea_id)

            all_cards.append(card_from_m3(
                evaluation,
                raw_m1_parent=raw_m1_lookup[key],
                domain=domain,
                source_file=source,
                max_chars=args.max_chars,
            ))

        expected_ids = {idea_id for dom, idea_id in raw_m1_lookup if dom == norm_domain(domain)}
        if seen_m3_ids != expected_ids:
            raise ValueError(
                f"M1/M3 IDs do not match in domain {domain}. "
                f"Missing M3={sorted(expected_ids - seen_m3_ids)}, "
                f"unexpected M3={sorted(seen_m3_ids - expected_ids)}"
            )

        input_manifest_rows.append({
            "condition": "M3",
            "domain": domain,
            "path": str(source.resolve()),
            "sha256": sha256_file(source),
            "payload_count": str(len(items)),
        })

    expected_total = len(domains) * 3 * args.ideas_per_condition_domain
    if len(all_cards) != expected_total:
        raise ValueError(f"Expected {expected_total} cards, built {len(all_cards)}.")

    method_counts = Counter(card["_method"] for card in all_cards)
    expected_per_method = len(domains) * args.ideas_per_condition_domain
    for method, count in method_counts.items():
        if count != expected_per_method:
            raise ValueError(f"Unexpected count for {method}: {count}, expected {expected_per_method}")

    rng = random.Random(args.seed)
    rng.shuffle(all_cards)
    for idx, card in enumerate(all_cards, start=1):
        card["_blind_id"] = f"BLIND_{idx:04d}"

    # Private mapping: never send this file to a judge.
    private_key = out_dir / "blind_key_private.csv"
    with private_key.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=[
            "candidate_id", "prompt_regime", "method_true", "domain",
            "original_idea_id", "pair_key", "source_file",
        ])
        writer.writeheader()
        for card in all_cards:
            writer.writerow({
                "candidate_id": card["_blind_id"],
                "prompt_regime": "P1_shared_neutral",
                "method_true": card["_method"],
                "domain": card["_domain"],
                "original_idea_id": card["_idea_id"],
                "pair_key": card["_pair_key"],
                "source_file": card["_source_file"],
            })

    # Public anonymized cards.
    write_jsonl(out_dir / "blind_candidates_public.jsonl", (public_card(card) for card in all_cards))

    # Public batches, with M1/M3 siblings separated.
    batches = build_batches(all_cards, args.batch_size, rng)
    for idx, batch in enumerate(batches, start=1):
        (batch_dir / f"batch_{idx:03d}.json").write_text(
            json.dumps([public_card(card) for card in batch], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # Audit whether siblings accidentally land in the same batch.
    sibling_collision_count = 0
    for batch in batches:
        pair_keys = [card["_pair_key"] for card in batch if card["_pair_key"]]
        sibling_collision_count += sum(count - 1 for count in Counter(pair_keys).values() if count > 1)

    with (out_dir / "input_file_manifest_sha256.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=["condition", "domain", "path", "sha256", "payload_count"])
        writer.writeheader()
        writer.writerows(input_manifest_rows)

    manifest = {
        "stage": "P1_blind_individual_dataset_builder",
        "prompt_regime": "P1_shared_neutral",
        "created_at_utc": now_iso(),
        "input_root": str(input_root),
        "seed": args.seed,
        "batch_size": args.batch_size,
        "expected_domains": args.expected_domains,
        "discovered_domains": len(domains),
        "ideas_per_condition_domain": args.ideas_per_condition_domain,
        "candidate_count_total": len(all_cards),
        "candidate_count_by_method": dict(method_counts),
        "batch_count": len(batches),
        "sibling_collisions_within_batch": sibling_collision_count,
        "public_files": [
            "blind_candidates_public.jsonl",
            "batches/batch_*.json",
            "input_file_manifest_sha256.csv",
            "manifest.json",
        ],
        "private_files_never_share_with_judge": ["blind_key_private.csv"],
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "README_PRIVATE_WARNING.txt").write_text(
        "KEEP PRIVATE: blind_key_private.csv contains method labels and pair mappings. "
        "Never upload it to Gemini, ChatGPT, Claude, or any external judge.\n",
        encoding="utf-8",
    )

    print("[OK] P1 blind dataset created")
    print(f"  candidates: {len(all_cards)}")
    print(f"  methods: {dict(method_counts)}")
    print(f"  batches: {len(batches)} (batch size ≤ {args.batch_size})")
    print(f"  private key: {private_key}")
    print(f"  public batches: {batch_dir}")
    print("  Next: run the Gemini judge using ONLY the public batches folder.")


if __name__ == "__main__":
    main()
