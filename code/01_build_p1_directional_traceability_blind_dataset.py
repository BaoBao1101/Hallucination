#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CHVD P1 — Build Directional Traceability Paired Dataset
========================================================

Purpose
-------
Build matched M1 -> M3 directional traceability pairs for P1.

This protocol is:
- paired: each M3 is matched to its source M1 by idea_id;
- directional: the evaluator sees which card is the Reference Concept and
  which card is the Transformation Candidate;
- method-blind: the evaluator never sees M1/M3, model, temperature, prompts,
  source filenames, or hidden method labels;
- shuffled: pair order is randomized and the visible display order of the two
  role-labelled cards is counterbalanced.

Important methodological note
-----------------------------
A directional traceability audit cannot be fully blind to source/transformation
direction. Metrics such as creative_core_preservation and semantic_drift_risk
require the evaluator to know which concept is the reference and which one is
the transformation.

Therefore this script hides METHOD identity, but preserves ROLE identity:

    Reference Concept       = matched source-side concept
    Transformation Candidate = matched transformed concept

Do NOT call this a neutral A/B preference experiment.
Use it as a directional traceability / preservation audit.

Expected input
--------------
outputs_P1/
  M1_<domain>/
      one JSON containing {"ideas": [...]}
  M3_<domain>/
      one JSON containing {"evaluations": [...]}

Default validation:
  7 domains × 5 matched M1/M3 pairs = 35 pairs

Public outputs safe for an evaluator
------------------------------------
  public_directional_batches/
  directional_traceability_prompt_vi.txt
  directional_traceability_output_schema.json
  public_manifest.json
  README_PUBLIC.md

Private outputs — never send to evaluators
------------------------------------------
  directional_key_private.csv
  input_manifest_private_sha256.csv
  manifest_private.json
  README_PRIVATE_WARNING.md

Standard-library only.
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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple


PROMPT_REGIME = "P1_shared_neutral"
M1_METHOD = "M1_RAW_HIGH_TEMP"
M3_METHOD = "M3_CHVD_DISTILLED"

REFERENCE_ROLE = "REFERENCE_CONCEPT"
TRANSFORMATION_ROLE = "TRANSFORMATION_CANDIDATE"

CARD_FIELDS = (
    "title",
    "target_user",
    "problem_or_desire",
    "core_concept",
    "proposed_approach",
    "prototype_scope",
)

DIRECTIONAL_METRICS = [
    {
        "name": "creative_core_preservation",
        "direction": "higher_is_better",
        "description": (
            "Transformation Candidate có giữ distinctive causal mechanism, experiential interaction, "
            "hoặc imaginative premise trung tâm của Reference Concept không?"
        ),
    },
    {
        "name": "novelty_retention",
        "direction": "higher_is_better",
        "description": (
            "Transformation Candidate có còn giữ phần khác biệt, không-rập-khuôn, và creative leap "
            "quan trọng của Reference Concept không?"
        ),
    },
    {
        "name": "practical_mvp_improvement",
        "direction": "higher_is_better",
        "description": (
            "Transformation Candidate có biến ý tưởng thành MVP rõ hơn, hẹp hơn, khả thi hơn, "
            "và dễ kiểm thử hơn không?"
        ),
    },
    {
        "name": "overclaim_removal_quality",
        "direction": "higher_is_better",
        "description": (
            "Transformation Candidate có loại bỏ hoặc hạ cấp unsupported claims, vague claims, "
            "và overclaims tốt không?"
        ),
    },
    {
        "name": "semantic_drift_risk",
        "direction": "lower_is_better",
        "description": (
            "Transformation Candidate có bị lệch khỏi central mechanism hoặc thay thế Reference Concept "
            "bằng một ý tưởng khác/generic khác không?"
        ),
    },
]


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> Dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"Cannot parse JSON: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"Top-level JSON must be an object: {path}")
    return payload


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "; ".join(part for part in (clean_text(x) for x in value) if part)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return re.sub(r"\s+", " ", str(value)).strip()


def first_nonempty(item: Mapping[str, Any], *keys: str, default: str = "") -> str:
    for key in keys:
        value = clean_text(item.get(key))
        if value:
            return value
    return default


def clip(value: str, max_chars: int) -> str:
    value = clean_text(value)
    return value if len(value) <= max_chars else value[: max_chars - 1].rstrip() + "…"


def norm_domain(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", clean_text(value).lower())


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: Sequence[Mapping[str, Any]], fields: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields))
        writer.writeheader()
        writer.writerows(rows)


def chunks(values: Sequence[Any], size: int) -> Iterable[List[Any]]:
    for start in range(0, len(values), size):
        yield list(values[start:start + size])


def condition_domain_from_path(json_path: Path, root: Path) -> Tuple[str, str] | None:
    try:
        folder_parts = json_path.relative_to(root).parts[:-1]
    except ValueError:
        return None

    for folder in reversed(folder_parts):
        match = re.fullmatch(r"(M1|M3)_(.+)", folder, flags=re.IGNORECASE)
        if match:
            return match.group(1).upper(), match.group(2).strip()
    return None


def expected_payload(doc: Mapping[str, Any], condition: str) -> bool:
    return isinstance(doc.get("ideas"), list) if condition == "M1" else isinstance(doc.get("evaluations"), list)


def discover_payloads(root: Path) -> Dict[Tuple[str, str], Path]:
    found: Dict[Tuple[str, str], Path] = {}

    for path in sorted(root.rglob("*.json")):
        info = condition_domain_from_path(path, root)
        if info is None:
            continue

        condition, domain = info
        try:
            doc = load_json(path)
        except Exception:
            continue

        if not expected_payload(doc, condition):
            continue

        key = (condition, norm_domain(domain))
        if key in found:
            raise ValueError(
                "More than one valid JSON payload for the same condition/domai[LOCAL_PATH_REDACTED]"
                f"  first: {found[key]}\n"
                f"  second: {path}\n"
                "Keep one payload JSON per M1_<domain> and M3_<domain>."
            )
        found[key] = path

    if not found:
        raise FileNotFoundError(
            f"No valid M1/M3 payloads found under: {root}\n"
            "Expected M1_<domain>/ and M3_<domain>/ folders."
        )
    return found


def m1_card(item: Mapping[str, Any], domain: str, source_file: Path, max_chars: int) -> Dict[str, str]:
    idea_id = first_nonempty(item, "idea_id", "source_idea_id", "candidate_id")
    if not idea_id:
        raise ValueError(f"M1 item has no idea_id: {source_file}")

    return {
        "title": clip(first_nonempty(item, "title", "idea_name"), max_chars),
        "target_user": clip(first_nonempty(item, "target_user"), max_chars),
        "problem_or_desire": clip(first_nonempty(item, "problem_or_desire", "hidden_pain_or_desire"), max_chars),
        "core_concept": clip(first_nonempty(item, "core_concept", "creative_leap", "one_sentence_pitch"), max_chars),
        "proposed_approach": clip(first_nonempty(item, "proposed_approach", "proposed_solution"), max_chars),
        "prototype_scope": clip(first_nonempty(item, "prototype_scope", "mvp_seed", "first_7_day_prototype"), max_chars),
        "_idea_id": idea_id,
        "_domain": domain,
        "_method": M1_METHOD,
        "_source_file": str(source_file.resolve()),
    }


def m3_card(item: Mapping[str, Any], reference_card: Mapping[str, str], source_file: Path, max_chars: int) -> Dict[str, str]:
    idea_id = first_nonempty(item, "idea_id", "source_idea_id", "candidate_id")
    if not idea_id:
        raise ValueError(f"M3 item has no idea_id: {source_file}")
    if idea_id != reference_card["_idea_id"]:
        raise ValueError(
            f"Matched ID mismatch: M1={reference_card['_idea_id']!r}; M3={idea_id!r}; file={source_file}"
        )

    practical_mvp = first_nonempty(item, "practical_mvp", "prototype_scope")
    validation_plan = first_nonempty(item, "mvp_7_to_30_day_plan", "validation_needed_later")
    scope = " | ".join(part for part in (practical_mvp, validation_plan) if part)

    return {
        "title": clip(first_nonempty(item, "title", default=reference_card["title"]), max_chars),
        "target_user": clip(first_nonempty(item, "target_user", default=reference_card["target_user"]), max_chars),
        "problem_or_desire": clip(
            first_nonempty(item, "problem_or_desire", default=reference_card["problem_or_desire"]),
            max_chars,
        ),
        "core_concept": clip(
            first_nonempty(item, "creative_core_to_preserve", "core_concept", default=reference_card["core_concept"]),
            max_chars,
        ),
        "proposed_approach": clip(
            first_nonempty(item, "realistic_reframing", "practical_mvp", "proposed_approach"),
            max_chars,
        ),
        "prototype_scope": clip(scope or reference_card["prototype_scope"], max_chars),
        "_idea_id": idea_id,
        "_domain": reference_card["_domain"],
        "_method": M3_METHOD,
        "_source_file": str(source_file.resolve()),
    }


def public_card(card: Mapping[str, str]) -> Dict[str, str]:
    return {field: card[field] for field in CARD_FIELDS}


def public_role_card(role_id: str, card: Mapping[str, str]) -> Dict[str, Any]:
    if role_id == REFERENCE_ROLE:
        role_name = "Reference Concept"
        evaluator_instruction = (
            "Use this concept only as the reference point for judging preservation, retention, "
            "MVP improvement, overclaim removal, and semantic drift."
        )
    elif role_id == TRANSFORMATION_ROLE:
        role_name = "Transformation Candidate"
        evaluator_instruction = (
            "Assess how this candidate changes, preserves, narrows, or operationalizes the Reference Concept. "
            "Do not assume this transformation is automatically better."
        )
    else:
        raise ValueError(f"Unknown role: {role_id}")

    return {
        "role_id": role_id,
        "role_name": role_name,
        "role_instruction": evaluator_instruction,
        "candidate": public_card(card),
    }


def directional_prompt() -> str:
    schema = {
        "evaluations": [
            {
                "pair_id": "P1TRACE_0001",
                "scores": {
                    "creative_core_preservation": 1.0,
                    "novelty_retention": 1.0,
                    "practical_mvp_improvement": 1.0,
                    "overclaim_removal_quality": 1.0,
                    "semantic_drift_risk": 1.0,
                },
                "preferred_for_prototype": "TRANSFORMATION",
                "short_rationale": "",
            }
        ]
    }

    return f"""# CHVD P1 — Directional Traceability Evaluation

You are an independent evaluator for a matched venture-concept transformation study.

Each pair contains:

- `Reference Concept`: the concept used as the reference point.
- `Transformation Candidate`: a candidate derived from or reformulated from the reference.

The visual order of the two cards is randomized. Always use the role labels, not display position.

## Blindness rules

Do not infer, mention, or speculate about:

- hidden generation methods;
- model names;
- temperature or sampling settings;
- prompts, pipelines, baselines, M1, M3, CHVD, or any source file;
- whether the transformation is necessarily better.

You may use only the text in the pair.

## Goal

Assess whether the Transformation Candidate preserves the distinctive creative mechanism of the Reference Concept while converting it into a more bounded, realistic, testable MVP.

## Score scale

For every numeric metric, use exactly one decimal from `1.0` through `5.0`.

Do not use integers automatically. Use decimal values when a textual distinction is supported.

### Creative-core preservation — higher is better

- 1.0: Central mechanism has been replaced by an unrelated idea.
- 2.0: Only topic or surface wording remains; central mechanism is mostly lost.
- 3.0: Part of the core remains, but a major mechanism changed.
- 4.0: Central mechanism is clearly retained while scope is narrowed.
- 5.0: Distinctive mechanism is retained almost completely and concretely operationalized.

### Novelty retention — higher is better

- 1.0: Transformation is generic and loses the distinguishing leap.
- 2.0: Mostly conventional, with little distinctive character remaining.
- 3.0: Some distinctive element remains, but novelty is substantially reduced.
- 4.0: Most important novelty is retained.
- 5.0: The transformation retains the distinctive novelty almost intact.

### Practical MVP improvement — higher is better

- 1.0: No improvement or transformation is less buildable.
- 2.0: Minor narrowing but no credible MVP boundary.
- 3.0: Moderate improvement in scope or implementation specificity.
- 4.0: Clearly more feasible, bounded, and testable.
- 5.0: Strong transformation into a concrete near-term MVP with credible scope.

### Overclaim-removal quality — higher is better

- 1.0: Unsupported claims remain or become worse.
- 2.0: Few overclaims are softened.
- 3.0: Some important overclaims are removed.
- 4.0: Most major unsupported claims are bounded or removed.
- 5.0: Overclaims are systematically replaced by careful, testable assumptions.

### Semantic-drift risk — lower is better

- 1.0: Almost no drift; clearly the same central concept.
- 2.0: Minor reframing, but the core is still recognizable.
- 3.0: Material change to the mechanism, though relation remains visible.
- 4.0: Transformation largely behaves as a different concept.
- 5.0: The reference concept is effectively replaced by an unrelated/generic concept.

## Prototype preference

Choose exactly one:

- `REFERENCE`: the Reference Concept should be prototyped first.
- `TRANSFORMATION`: the Transformation Candidate should be prototyped first.
- `TIE`: neither is clearly preferable.

Do not assume the Transformation Candidate must win.

## Response requirements

1. Evaluate every pair independently.
2. Return every pair exactly once.
3. Rationale must be specific to the pair and no more than 50 words.
4. Return valid JSON only. No markdown, no code fences, no text outside JSON.

## Required schema

```json
{json.dumps(schema, ensure_ascii=False, indent=2)}
```
"""


def build_pairs(
    payloads: Mapping[Tuple[str, str], Path],
    expected_domains: int,
    ideas_per_domain: int,
    max_field_chars: int,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    m1_domains = {domain for condition, domain in payloads if condition == "M1"}
    m3_domains = {domain for condition, domain in payloads if condition == "M3"}

    if m1_domains != m3_domains:
        raise ValueError(
            f"M1/M3 domain mismatch. M1-only={sorted(m1_domains - m3_domains)}; "
            f"M3-only={sorted(m3_domains - m1_domains)}"
        )

    if expected_domains and len(m1_domains) != expected_domains:
        raise ValueError(f"Expected {expected_domains} domains but found {len(m1_domains)}: {sorted(m1_domains)}")

    pairs: List[Dict[str, Any]] = []
    manifest_rows: List[Dict[str, Any]] = []

    for domain in sorted(m1_domains):
        m1_path = payloads[("M1", domain)]
        m3_path = payloads[("M3", domain)]

        m1_doc = load_json(m1_path)
        m3_doc = load_json(m3_path)

        source_cards: Dict[str, Dict[str, str]] = {}
        for item in m1_doc["ideas"]:
            if not isinstance(item, dict):
                raise ValueError(f"Non-object M1 item in {m1_path}")
            card = m1_card(item, domain, m1_path, max_field_chars)
            if card["_idea_id"] in source_cards:
                raise ValueError(f"Duplicate M1 idea_id in {m1_path}: {card['_idea_id']}")
            source_cards[card["_idea_id"]] = card

        transformed_items: Dict[str, Mapping[str, Any]] = {}
        for item in m3_doc["evaluations"]:
            if not isinstance(item, dict):
                raise ValueError(f"Non-object M3 item in {m3_path}")
            idea_id = first_nonempty(item, "idea_id", "source_idea_id", "candidate_id")
            if not idea_id:
                raise ValueError(f"M3 item without idea_id in {m3_path}")
            if idea_id in transformed_items:
                raise ValueError(f"Duplicate M3 idea_id in {m3_path}: {idea_id}")
            transformed_items[idea_id] = item

        if set(source_cards) != set(transformed_items):
            raise ValueError(
                f"Unmatched IDs in domain {domain}: "
                f"M1-only={sorted(set(source_cards) - set(transformed_items))}; "
                f"M3-only={sorted(set(transformed_items) - set(source_cards))}"
            )

        if ideas_per_domain and len(source_cards) != ideas_per_domain:
            raise ValueError(
                f"Expected {ideas_per_domain} pairs in {domain}, found {len(source_cards)}."
            )

        for idea_id in sorted(source_cards):
            ref = source_cards[idea_id]
            transformed = m3_card(transformed_items[idea_id], ref, m3_path, max_field_chars)
            pairs.append({
                "_domain": domain,
                "_idea_id": idea_id,
                "_reference": ref,
                "_transformation": transformed,
            })

        manifest_rows.extend([
            {
                "role": "reference_side_hidden_method",
                "domain": domain,
                "path": str(m1_path.resolve()),
                "sha256": sha256_file(m1_path),
                "bytes": m1_path.stat().st_size,
            },
            {
                "role": "transformation_side_hidden_method",
                "domain": domain,
                "path": str(m3_path.resolve()),
                "sha256": sha256_file(m3_path),
                "bytes": m3_path.stat().st_size,
            },
        ])

    total_expected = expected_domains * ideas_per_domain if expected_domains and ideas_per_domain else None
    if total_expected is not None and len(pairs) != total_expected:
        raise ValueError(f"Expected {total_expected} matched pairs but built {len(pairs)}.")

    return pairs, manifest_rows


def prepare_out_dir(out_dir: Path, rebuild: bool) -> None:
    if out_dir.exists() and any(out_dir.iterdir()):
        if not rebuild:
            raise FileExistsError(
                f"Output directory is non-empty: {out_dir}\n"
                "Use a new --out-dir, or pass --rebuild only before evaluation starts."
            )
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build role-aware, method-blind P1 directional traceability dataset."
    )
    parser.add_argument("--input-root", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--expected-domains", type=int, default=7)
    parser.add_argument("--ideas-per-domain", type=int, default=5)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--seed", type=int, default=20260623)
    parser.add_argument("--max-field-chars", type=int, default=1300)
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.batch_size < 1:
        raise ValueError("--batch-size must be >= 1")
    if args.max_field_chars < 150:
        raise ValueError("--max-field-chars must be >= 150")

    input_root = Path(args.input_root).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    if not input_root.exists():
        raise FileNotFoundError(f"Input root not found: {input_root}")

    payloads = discover_payloads(input_root)
    pairs, input_manifest_rows = build_pairs(
        payloads,
        args.expected_domains,
        args.ideas_per_domain,
        args.max_field_chars,
    )

    rng = random.Random(args.seed)
    rng.shuffle(pairs)

    # Counterbalance visual order while preserving role identity.
    # For n=35, this creates 17 reference-first and 18 transformation-first pairs.
    n_reference_first = len(pairs) // 2
    display_flags = [True] * n_reference_first + [False] * (len(pairs) - n_reference_first)
    rng.shuffle(display_flags)

    for index, (pair, reference_first) in enumerate(zip(pairs, display_flags), start=1):
        pair_id = f"P1TRACE_{index:04d}"
        reference_public = public_role_card(REFERENCE_ROLE, pair["_reference"])
        transformation_public = public_role_card(TRANSFORMATION_ROLE, pair["_transformation"])

        display_cards = (
            [reference_public, transformation_public]
            if reference_first
            else [transformation_public, reference_public]
        )

        pair["pair_id"] = pair_id
        pair["_reference_first"] = reference_first
        pair["displayed_cards"] = display_cards

    if args.dry_run:
        print("[DRY-RUN] Directional traceability validation passed.")
        print(f"Matched pairs: {len(pairs)}")
        print(f"Public batches: {math.ceil(len(pairs) / args.batch_size)}")
        print(
            "Display order balance: "
            f"Reference-first={sum(p['_reference_first'] for p in pairs)}; "
            f"Transformation-first={sum(not p['_reference_first'] for p in pairs)}"
        )
        print("No output files were written.")
        return

    prepare_out_dir(out_dir, args.rebuild)

    public_pairs = [
        {
            "pair_id": pair["pair_id"],
            "displayed_cards": pair["displayed_cards"],
        }
        for pair in pairs
    ]

    for batch_index, batch in enumerate(chunks(public_pairs, args.batch_size), start=1):
        write_json(
            out_dir / "public_directional_batches" / f"traceability_batch_{batch_index:03d}.json",
            {
                "batch_id": f"P1_DIRECTIONAL_TRACE_{batch_index:03d}",
                "stage": "directional_role_aware_method_blind_traceability",
                "prompt_regime": PROMPT_REGIME,
                "blindness_note": (
                    "Hidden method identity: the evaluator is not shown M1/M3, model, temperature, "
                    "prompt, source filename, or generation pipeline. "
                    "Visible directional role: Reference Concept versus Transformation Candidate."
                ),
                "pairs": batch,
            },
        )

    output_schema = {
        "score_range": "1.0 to 5.0 inclusive, exactly one decimal",
        "metrics": DIRECTIONAL_METRICS,
        "prototype_choice_values": ["REFERENCE", "TRANSFORMATION", "TIE"],
        "output_schema": {
            "evaluations": [
                {
                    "pair_id": "P1TRACE_0001",
                    "scores": {metric["name"]: 1.0 for metric in DIRECTIONAL_METRICS},
                    "preferred_for_prototype": "TRANSFORMATION",
                    "short_rationale": "",
                }
            ]
        },
    }
    write_json(out_dir / "directional_traceability_output_schema.json", output_schema)
    (out_dir / "directional_traceability_prompt_vi.txt").write_text(directional_prompt(), encoding="utf-8")

    private_rows = []
    for pair in pairs:
        private_rows.append({
            "pair_id": pair["pair_id"],
            "prompt_regime": PROMPT_REGIME,
            "domain": pair["_domain"],
            "source_idea_id": pair["_idea_id"],
            "reference_hidden_method": pair["_reference"]["_method"],
            "transformation_hidden_method": pair["_transformation"]["_method"],
            "reference_source_file": pair["_reference"]["_source_file"],
            "transformation_source_file": pair["_transformation"]["_source_file"],
            "reference_displayed_first": pair["_reference_first"],
        })

    write_csv(
        out_dir / "directional_key_private.csv",
        private_rows,
        (
            "pair_id",
            "prompt_regime",
            "domain",
            "source_idea_id",
            "reference_hidden_method",
            "transformation_hidden_method",
            "reference_source_file",
            "transformation_source_file",
            "reference_displayed_first",
        ),
    )
    write_csv(
        out_dir / "input_manifest_private_sha256.csv",
        input_manifest_rows,
        ("role", "domain", "path", "sha256", "bytes"),
    )

    public_manifest = {
        "created_at_utc": utc_now(),
        "prompt_regime": PROMPT_REGIME,
        "protocol_type": "Directional traceability audit: role-aware, method-blind, display-order-counterbalanced.",
        "pair_count": len(pairs),
        "public_batch_size": args.batch_size,
        "public_batch_count": math.ceil(len(pairs) / args.batch_size),
        "visible_roles": ["Reference Concept", "Transformation Candidate"],
        "hidden_information": [
            "M1/M3 labels",
            "model identity",
            "temperature",
            "prompt text",
            "source filenames",
            "generation pipeline",
        ],
        "display_order_counterbalance": {
            "reference_first": sum(pair["_reference_first"] for pair in pairs),
            "transformation_first": sum(not pair["_reference_first"] for pair in pairs),
        },
        "metrics": [metric["name"] for metric in DIRECTIONAL_METRICS],
        "warning": (
            "This is not a neutral preference experiment. Direction is visible by design because "
            "preservation and semantic-drift metrics require a reference/transformation relationship."
        ),
    }
    write_json(out_dir / "public_manifest.json", public_manifest)
    write_json(
        out_dir / "manifest_private.json",
        {
            **public_manifest,
            "randomization_seed": args.seed,
            "input_root": str(input_root),
            "private_key": "directional_key_private.csv",
            "private_input_manifest": "input_manifest_private_sha256.csv",
        },
    )

    (out_dir / "README_PUBLIC.md").write_text(
        f"""# CHVD P1 Directional Traceability Dataset — Public Evaluator Package

## Protocol

This package contains **{len(pairs)} matched directional pairs**.

Each pair has two visible roles:

- `Reference Concept`
- `Transformation Candidate`

The displayed order of the two role-labelled cards is randomized and counterbalanced.
The pair sequence is shuffled.

## What is hidden

The evaluator does not receive:

- M1/M3 labels;
- model name;
- temperature;
- prompts;
- source filenames;
- generation pipeline.

## Important limitation

This is a directional traceability audit, not a neutral A/B preference test.

The evaluator must know which card is the reference and which is the transformation
to assess creative-core preservation, novelty retention, practical MVP improvement,
overclaim removal, and semantic drift.

Use `directional_traceability_prompt_vi.txt` and
`directional_traceability_output_schema.json` with the public batch files.
""",
        encoding="utf-8",
    )

    (out_dir / "README_PRIVATE_WARNING.md").write_text(
        """# PRIVATE FILE WARNING

Never upload, paste, or share these files with Gemini, other LLMs, or human evaluators:

- directional_key_private.csv
- input_manifest_private_sha256.csv
- manifest_private.json

They expose hidden source provenance and M1/M3 method mapping.

The evaluator-safe files are:

- public_directional_batches/
- directional_traceability_prompt_vi.txt
- directional_traceability_output_schema.json
- public_manifest.json
- README_PUBLIC.md
""",
        encoding="utf-8",
    )

    # Guard against accidental method labels in public JSON metadata/content.
    public_text = json.dumps(public_pairs, ensure_ascii=False)
    forbidden_terms = (M1_METHOD, M3_METHOD, "M1_", "M3_")
    for term in forbidden_terms:
        if term in public_text:
            raise RuntimeError(f"Public export unexpectedly contains hidden method term: {term}")

    print("[OK] P1 directional traceability package created.")
    print(f"[OK] Matched pairs: {len(pairs)}")
    print(f"[OK] Public batches: {math.ceil(len(pairs) / args.batch_size)}")
    print(
        "[OK] Display order: "
        f"Reference-first={sum(p['_reference_first'] for p in pairs)}, "
        f"Transformation-first={sum(not p['_reference_first'] for p in pairs)}"
    )
    print(f"[OK] Output: {out_dir}")
    print("[WARNING] Do not send private files to evaluators.")


if __name__ == "__main__":
    main()
