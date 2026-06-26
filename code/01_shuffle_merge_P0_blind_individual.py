#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CHVD Blind Evaluation Toolkit — Step 1

Merge raw high-temperature, CHVD distilled, and optional direct low-temperature
ideas into a single blinded dataset for independent evaluation.

Inputs
------
- Raw high-temperature files: top-level key "ideas"
- CHVD filtered files:       top-level key "evaluations"
- Direct low-temperature:    top-level key "ideas" (optional)

Outputs
-------
- blind_candidates.jsonl        SAFE for an independent judge
- batches/batch_XXX.json        SAFE for an independent judge
- blind_key_private.csv         NEVER share with the judge
- pairwise_distillation_input.jsonl  SAFE for pairwise judge
- pairwise_key_private.csv      NEVER share with the pairwise judge
- manifest.json                 audit record

The script matches raw and CHVD-filtered records by (normalized domain, idea_id).
It intentionally assigns new blind IDs after deterministic shuffling.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


RAW_METHOD = "M1_RAW_HIGH_TEMP"
CHVD_METHOD = "M3_CHVD_DISTILLED"
LOW_METHOD = "M0_DIRECT_LOW_TEMP"


def load_json(path: str | Path) -> Dict[str, Any]:
    p = Path(path)
    try:
        value = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"Không đọc được JSON: {p}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"JSON phải là object top-level: {p}")
    return value


def text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return re.sub(r"\s+", " ", value).strip()
    if isinstance(value, list):
        return "; ".join(text(x) for x in value if text(x))
    if isinstance(value, dict):
        return "; ".join(f"{k}: {text(v)}" for k, v in value.items() if text(v))
    return str(value).strip()


def clip(value: Any, max_chars: int) -> str:
    s = text(value)
    if len(s) <= max_chars:
        return s
    return s[: max(1, max_chars - 1)].rstrip() + "…"


def first_nonempty(*values: Any) -> str:
    for value in values:
        candidate = text(value)
        if candidate:
            return candidate
    return ""


def norm_domain(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def slug(value: str) -> str:
    result = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return result or "unknown_domain"


def extract_domain(doc: Dict[str, Any], fallback: str) -> str:
    metadata = doc.get("metadata", {})
    metadata = metadata if isinstance(metadata, dict) else {}
    source_metadata = metadata.get("source_generator_metadata", {})
    source_metadata = source_metadata if isinstance(source_metadata, dict) else {}
    return first_nonempty(metadata.get("domain"), source_metadata.get("domain"), fallback)


def raw_items(doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    values = doc.get("ideas", [])
    if not isinstance(values, list):
        raise ValueError("Raw/low-temp JSON cần top-level key 'ideas' là list.")
    return [x for x in values if isinstance(x, dict)]


def filtered_items(doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    values = doc.get("evaluations", [])
    if not isinstance(values, list):
        raise ValueError("Filtered JSON cần top-level key 'evaluations' là list.")
    return [x for x in values if isinstance(x, dict)]


def make_pair_key(domain: str, idea_id: str) -> str:
    return f"PAIR::{slug(domain)}::{idea_id}"


def raw_card(item: Dict[str, Any], *, domain: str, method: str, source_file: str, max_chars: int) -> Dict[str, Any]:
    idea_id = first_nonempty(item.get("idea_id"), "UNKNOWN")
    title = first_nonempty(item.get("idea_name"), f"Idea {idea_id}")

    return {
        "_method": method,
        "_source_file": str(source_file),
        "_original_idea_id": idea_id,
        "_pair_key": make_pair_key(domain, idea_id) if method == RAW_METHOD else "",
        "domain": domain,
        "title": clip(title, max_chars),
        "problem_or_desire": clip(first_nonempty(item.get("hidden_pain_or_desire"), item.get("target_user")), max_chars),
        "core_concept": clip(first_nonempty(item.get("one_sentence_pitch"), item.get("creative_leap")), max_chars),
        "proposed_approach": clip(first_nonempty(item.get("proposed_solution"), item.get("creative_leap")), max_chars),
        "prototype_scope": clip(first_nonempty(item.get("mvp_seed"), item.get("first_7_day_prototype")), max_chars),
    }


def distilled_card(
    item: Dict[str, Any], *, domain: str, source_file: str, raw_parent: Dict[str, Any], max_chars: int
) -> Dict[str, Any]:
    idea_id = first_nonempty(item.get("idea_id"), "UNKNOWN")
    title = first_nonempty(item.get("idea_name"), f"Idea {idea_id}")
    inherited_problem = first_nonempty(raw_parent.get("hidden_pain_or_desire"), raw_parent.get("target_user"))

    return {
        "_method": CHVD_METHOD,
        "_source_file": str(source_file),
        "_original_idea_id": idea_id,
        "_pair_key": make_pair_key(domain, idea_id),
        "domain": domain,
        "title": clip(title, max_chars),
        "problem_or_desire": clip(first_nonempty(inherited_problem, item.get("raw_hallucination_summary")), max_chars),
        "core_concept": clip(first_nonempty(item.get("creative_core_to_preserve"), item.get("realistic_reframing")), max_chars),
        "proposed_approach": clip(first_nonempty(item.get("realistic_reframing"), item.get("creative_core_to_preserve")), max_chars),
        "prototype_scope": clip(first_nonempty(item.get("practical_mvp"), item.get("mvp_7_to_30_day_plan")), max_chars),
    }


def public_card(card: Dict[str, Any]) -> Dict[str, Any]:
    """Only neutral fields are exposed to the independent judge."""
    return {
        "candidate_id": card["_blind_id"],
        "domain": card["domain"],
        "title": card["title"],
        "problem_or_desire": card["problem_or_desire"],
        "core_concept": card["core_concept"],
        "proposed_approach": card["proposed_approach"],
        "prototype_scope": card["prototype_scope"],
    }


def write_jsonl(path: Path, records: Iterable[Dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def build_batches(cards: List[Dict[str, Any]], batch_size: int, rng: random.Random) -> List[List[Dict[str, Any]]]:
    """Attempt to keep paired raw/distilled cards in different individual-judge batches."""
    if batch_size <= 0:
        return [cards]

    num_batches = math.ceil(len(cards) / batch_size)
    buckets: List[List[Dict[str, Any]]] = [[] for _ in range(num_batches)]
    pending = cards.copy()
    rng.shuffle(pending)

    for card in pending:
        pair_key = card.get("_pair_key", "")
        choices: List[int] = []
        for index, bucket in enumerate(buckets):
            if len(bucket) >= batch_size:
                continue
            existing_pairs = {x.get("_pair_key", "") for x in bucket if x.get("_pair_key", "")}
            if pair_key and pair_key in existing_pairs:
                continue
            choices.append(index)

        if not choices:
            choices = [i for i, bucket in enumerate(buckets) if len(bucket) < batch_size]
        if not choices:
            raise RuntimeError("Không còn slot trống khi tạo batch.")

        min_size = min(len(buckets[i]) for i in choices)
        balanced = [i for i in choices if len(buckets[i]) == min_size]
        buckets[rng.choice(balanced)].append(card)

    return [bucket for bucket in buckets if bucket]


def main() -> None:
    parser = argparse.ArgumentParser(description="Merge + shuffle CHVD candidates for blind evaluation.")
    parser.add_argument("--raw", nargs="+", required=True, help="High-temperature JSON files (top-level 'ideas').")
    parser.add_argument("--filtered", nargs="+", required=True, help="CHVD filtered JSON files (top-level 'evaluations').")
    parser.add_argument("--low-temp", nargs="*", default=[], help="Optional direct low-temperature JSON files (top-level 'ideas').")
    parser.add_argument("--out-dir", default="blind_eval_output")
    parser.add_argument("--seed", type=int, default=20260621)
    parser.add_argument("--batch-size", type=int, default=5)
    parser.add_argument("--max-chars", type=int, default=480, help="Maximum characters per public card field.")
    args = parser.parse_args()

    if args.batch_size < 1:
        raise ValueError("--batch-size phải >= 1")

    out_dir = Path(args.out_dir)
    batch_dir = out_dir / "batches"
    out_dir.mkdir(parents=True, exist_ok=True)
    batch_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(args.seed)

    raw_parent_lookup: Dict[Tuple[str, str], Dict[str, Any]] = {}
    all_cards: List[Dict[str, Any]] = []
    count_raw = count_filtered = count_low = 0

    for raw_file in args.raw:
        doc = load_json(raw_file)
        domain = extract_domain(doc, Path(raw_file).stem)
        for item in raw_items(doc):
            idea_id = first_nonempty(item.get("idea_id"), "UNKNOWN")
            key = (norm_domain(domain), idea_id)
            if key in raw_parent_lookup:
                raise ValueError(f"Trùng raw parent key {key}; kiểm tra domain/idea_id.")
            raw_parent_lookup[key] = item
            all_cards.append(raw_card(item, domain=domain, method=RAW_METHOD, source_file=raw_file, max_chars=args.max_chars))
            count_raw += 1

    for filtered_file in args.filtered:
        doc = load_json(filtered_file)
        domain = extract_domain(doc, Path(filtered_file).stem)
        for item in filtered_items(doc):
            idea_id = first_nonempty(item.get("idea_id"), "UNKNOWN")
            parent = raw_parent_lookup.get((norm_domain(domain), idea_id))
            if parent is None:
                raise ValueError(
                    f"Không tìm thấy raw parent cho filtered idea: domain='{domain}', idea_id='{idea_id}'. "
                    "Raw và filtered phải đến từ cùng domain/run."
                )
            all_cards.append(distilled_card(item, domain=domain, source_file=filtered_file, raw_parent=parent, max_chars=args.max_chars))
            count_filtered += 1

    for low_file in args.low_temp:
        doc = load_json(low_file)
        domain = extract_domain(doc, Path(low_file).stem)
        for item in raw_items(doc):
            all_cards.append(raw_card(item, domain=domain, method=LOW_METHOD, source_file=low_file, max_chars=args.max_chars))
            count_low += 1

    if count_raw != count_filtered:
        raise ValueError(
            f"Số raw/filter không khớp: raw={count_raw}, filtered={count_filtered}. "
            "Mỗi raw high-temp phải có đúng một filtered counterpart."
        )

    # Remove prior generated batch files to avoid stale files during reruns.
    for stale in batch_dir.glob("batch_*.json"):
        stale.unlink()

    rng.shuffle(all_cards)
    for index, card in enumerate(all_cards, start=1):
        card["_blind_id"] = f"BLIND_{index:04d}"

    # Public individual cards and private key.
    write_jsonl(out_dir / "blind_candidates.jsonl", (public_card(card) for card in all_cards))

    with (out_dir / "blind_key_private.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["candidate_id", "method_true", "domain", "original_idea_id", "pair_key", "source_file"],
        )
        writer.writeheader()
        for card in all_cards:
            writer.writerow({
                "candidate_id": card["_blind_id"],
                "method_true": card["_method"],
                "domain": card["domain"],
                "original_idea_id": card["_original_idea_id"],
                "pair_key": card.get("_pair_key", ""),
                "source_file": card["_source_file"],
            })

    # Generate individual-judge batches. Raw/CHVD siblings are separated when possible.
    batches = build_batches(all_cards, args.batch_size, rng)
    for index, batch in enumerate(batches, start=1):
        (batch_dir / f"batch_{index:03d}.json").write_text(
            json.dumps([public_card(card) for card in batch], ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # Public pairwise source-vs-revision cards and private pair mapping.
    cards_by_pair: Dict[str, List[Dict[str, Any]]] = {}
    for card in all_cards:
        pair_key = card.get("_pair_key", "")
        if pair_key:
            cards_by_pair.setdefault(pair_key, []).append(card)

    pair_records_private: List[Dict[str, Any]] = []
    pair_records_public: List[Dict[str, Any]] = []
    for pair_key, siblings in sorted(cards_by_pair.items()):
        raw_card_item = next((x for x in siblings if x["_method"] == RAW_METHOD), None)
        chvd_card_item = next((x for x in siblings if x["_method"] == CHVD_METHOD), None)
        if raw_card_item is None or chvd_card_item is None:
            continue
        pair_records_private.append({
            "pair_key": pair_key,
            "domain": raw_card_item["domain"],
            "raw_candidate_id": raw_card_item["_blind_id"],
            "distilled_candidate_id": chvd_card_item["_blind_id"],
        })

    rng.shuffle(pair_records_private)
    for index, pair in enumerate(pair_records_private, start=1):
        pair_blind_id = f"PAIRBLIND_{index:04d}"
        pair["pair_id"] = pair_blind_id
        raw_card_item = next(card for card in all_cards if card["_blind_id"] == pair["raw_candidate_id"])
        chvd_card_item = next(card for card in all_cards if card["_blind_id"] == pair["distilled_candidate_id"])
        pair_records_public.append({
            "pair_id": pair_blind_id,
            "domain": pair["domain"],
            "version_a": public_card(raw_card_item),
            "version_b": public_card(chvd_card_item),
        })

    write_jsonl(out_dir / "pairwise_distillation_input.jsonl", pair_records_public)
    with (out_dir / "pairwise_key_private.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["pair_id", "pair_key", "domain", "raw_candidate_id", "distilled_candidate_id"],
        )
        writer.writeheader()
        writer.writerows(pair_records_private)

    manifest = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "seed": args.seed,
        "batch_size": args.batch_size,
        "max_chars_per_field": args.max_chars,
        "counts": dict(Counter(card["_method"] for card in all_cards)),
        "raw_high_temperature_count": count_raw,
        "chvd_distilled_count": count_filtered,
        "direct_low_temperature_count": count_low,
        "total_blind_candidates": len(all_cards),
        "pairwise_raw_to_distilled_pairs": len(pair_records_public),
        "notes": [
            "Send only blind_candidates.jsonl or batches/ to the individual judge.",
            "Never share blind_key_private.csv with any judge.",
            "Send only pairwise_distillation_input.jsonl to the pairwise judge.",
            "Never share pairwise_key_private.csv with any judge.",
        ],
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print("[OK] Đã tạo blind evaluation dataset")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    print(f"[OK] Public cards : {(out_dir / 'blind_candidates.jsonl').resolve()}")
    print(f"[OK] PRIVATE key : {(out_dir / 'blind_key_private.csv').resolve()}")
    print(f"[OK] Pairwise input: {(out_dir / 'pairwise_distillation_input.jsonl').resolve()}")


if __name__ == "__main__":
    main()
