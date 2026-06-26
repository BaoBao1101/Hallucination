#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CHVD P1 — Build Neutral, Randomized, Blinded M1-vs-M3 Pairwise Dataset
=========================================================================

PRIMARY PROTOCOL
----------------
This script creates a *neutral* A/B pairwise dataset:
    - each pair contains one matched M1 raw candidate and one matched M3 candidate;
    - A/B orientation is randomized independently per pair;
    - public files contain no method labels, no source/revision labels, and no private map;
    - evaluators choose A, B, or TIE for each pairwise metric.

Important:
- This primary neutral protocol cannot measure directional metrics such as
  creative_core_preservation or semantic_drift_risk, because those require
  knowing which candidate is M1 source and which is M3 distillation.
- The script also creates a PRIVATE directional-audit input for an optional,
  separate P0-style traceability audit. Do NOT treat that audit as neutral
  preference evidence.

Expected P1 input tree
----------------------
outputs_P1/
  M1_<domain>/
      one JSON with {"ideas": [...]}
  M3_<domain>/
      one JSON with {"evaluations": [...]}

Default P1 validation:
  7 domains × 5 matched M1/M3 pairs = 35 pairs

Public outputs safe for Gemini
------------------------------
  public_gemini_batches/pairwise_batch_XXX.json
  pairwise_neutral_prompt_vi.txt
  pairwise_neutral_output_schema.json
  public_manifest.json
  README_PUBLIC.md

Private outputs — NEVER upload to Gemini or human raters
---------------------------------------------------------
  pairwise_key_private.csv
  private_directional_traceability_input.jsonl
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
CARD_FIELDS = (
    "title",
    "target_user",
    "problem_or_desire",
    "core_concept",
    "proposed_approach",
    "prototype_scope",
)
CHOICES = ("A", "B", "TIE")

NEUTRAL_METRICS = [
    {
        "name": "novelty_preference",
        "direction": "higher_is_better",
        "description": "Candidate nào có cơ chế hoặc cách tiếp cận khác biệt, sáng tạo và ít rập khuôn hơn?",
    },
    {
        "name": "conceptual_coherence_preference",
        "direction": "higher_is_better",
        "description": "Candidate nào logic và nhất quán hơn giữa problem/desire, concept, approach và MVP?",
    },
    {
        "name": "user_pain_plausibility_preference",
        "direction": "higher_is_better",
        "description": "Candidate nào mô tả user pain hoặc desire hợp lý hơn như một giả thuyết cần kiểm thử?",
    },
    {
        "name": "technical_plausibility_preference",
        "direction": "higher_is_better",
        "description": "Candidate nào khả thi hơn về mặt kỹ thuật trong đúng phạm vi được mô tả?",
    },
    {
        "name": "mvp_clarity_preference",
        "direction": "higher_is_better",
        "description": "Candidate nào có MVP cụ thể, đủ hẹp và dễ kiểm thử hơn?",
    },
    {
        "name": "business_model_plausibility_preference",
        "direction": "higher_is_better",
        "description": "Candidate nào có giả thuyết value exchange hoặc business model hợp lý hơn?",
    },
    {
        "name": "lower_unsupported_claim_risk",
        "direction": "lower_is_better",
        "description": "Candidate nào có ít unsupported claim, vague claim hoặc overclaim hơn?",
    },
    {
        "name": "lower_ethical_privacy_risk",
        "direction": "lower_is_better",
        "description": "Candidate nào có rủi ro đạo đức, quyền riêng tư, thao túng hoặc misuse thấp hơn?",
    },
    {
        "name": "overall_mvp_usefulness_preference",
        "direction": "higher_is_better",
        "description": "Candidate nào hữu ích hơn như một MVP hypothesis để kiểm thử?",
    },
    {
        "name": "prototype_preference",
        "direction": "higher_is_better",
        "description": "Nếu nguồn lực chỉ đủ prototype một candidate trước, bạn chọn candidate nào?",
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


def write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


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
        folders = json_path.relative_to(root).parts[:-1]
    except ValueError:
        return None
    for folder in reversed(folders):
        match = re.fullmatch(r"(M1|M3)_(.+)", folder, flags=re.IGNORECASE)
        if match:
            return match.group(1).upper(), match.group(2).strip()
    return None


def is_expected_payload(doc: Mapping[str, Any], condition: str) -> bool:
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
        if not is_expected_payload(doc, condition):
            continue
        key = (condition, norm_domain(domain))
        if key in found:
            raise ValueError(
                "Found more than one valid JSON payload for the same condition/domai[LOCAL_PATH_REDACTED]"
                f"  first:  {found[key]}\n"
                f"  second: {path}\n"
                "Keep exactly one payload JSON per M1_<domain> and M3_<domain> folder."
            )
        found[key] = path
    if not found:
        raise FileNotFoundError(
            f"No P1 M1/M3 JSON payloads found below: {root}\n"
            "Expected folders named M1_<domain> and M3_<domain>."
        )
    return found


def make_m1_card(item: Mapping[str, Any], domain: str, source_file: Path, max_chars: int) -> Dict[str, str]:
    idea_id = first_nonempty(item, "idea_id", "source_idea_id", "candidate_id")
    if not idea_id:
        raise ValueError(f"M1 idea is missing idea_id: {source_file}")
    return {
        "title": clip(first_nonempty(item, "title", "idea_name"), max_chars),
        "target_user": clip(first_nonempty(item, "target_user"), max_chars),
        "problem_or_desire": clip(first_nonempty(item, "problem_or_desire", "hidden_pain_or_desire"), max_chars),
        "core_concept": clip(first_nonempty(item, "core_concept", "creative_leap", "one_sentence_pitch"), max_chars),
        "proposed_approach": clip(first_nonempty(item, "proposed_approach", "proposed_solution"), max_chars),
        "prototype_scope": clip(first_nonempty(item, "prototype_scope", "mvp_seed", "first_7_day_prototype"), max_chars),
        "_idea_id": idea_id,
        "_method": M1_METHOD,
        "_domain": domain,
        "_source_file": str(source_file.resolve()),
    }


def make_m3_card(item: Mapping[str, Any], m1_card: Mapping[str, str], source_file: Path, max_chars: int) -> Dict[str, str]:
    idea_id = first_nonempty(item, "idea_id", "source_idea_id", "candidate_id")
    if not idea_id:
        raise ValueError(f"M3 evaluation is missing idea_id: {source_file}")
    if idea_id != m1_card["_idea_id"]:
        raise ValueError(
            f"M1/M3 idea mismatch: M1={m1_card['_idea_id']!r}, M3={idea_id!r}; file={source_file}"
        )

    practical_mvp = first_nonempty(item, "practical_mvp", "prototype_scope")
    validation_plan = first_nonempty(item, "mvp_7_to_30_day_plan", "validation_needed_later")
    merged_scope = " | ".join(x for x in (practical_mvp, validation_plan) if x)

    return {
        "title": clip(first_nonempty(item, "title", default=m1_card["title"]), max_chars),
        "target_user": clip(first_nonempty(item, "target_user", default=m1_card["target_user"]), max_chars),
        "problem_or_desire": clip(first_nonempty(item, "problem_or_desire", default=m1_card["problem_or_desire"]), max_chars),
        "core_concept": clip(
            first_nonempty(item, "creative_core_to_preserve", "core_concept", default=m1_card["core_concept"]),
            max_chars,
        ),
        "proposed_approach": clip(
            first_nonempty(item, "realistic_reframing", "practical_mvp", "proposed_approach"),
            max_chars,
        ),
        "prototype_scope": clip(merged_scope or m1_card["prototype_scope"], max_chars),
        "_idea_id": idea_id,
        "_method": M3_METHOD,
        "_domain": m1_card["_domain"],
        "_source_file": str(source_file.resolve()),
    }


def public_card(card: Mapping[str, str]) -> Dict[str, str]:
    return {field: card[field] for field in CARD_FIELDS}


def neutral_prompt() -> str:
    metric_lines = "\n".join(
        f"- {m['name']}: {m['description']} "
        f"({'điểm thấp hơn là tốt hơn' if m['direction'] == 'lower_is_better' else 'điểm cao hơn là tốt hơn'})."
        for m in NEUTRAL_METRICS
    )
    schema = {
        "evaluations": [{
            "pair_id": "P1PAIR_0001",
            "preferences": {m["name"]: "A" for m in NEUTRAL_METRICS},
            "short_rationale": "",
        }]
    }
    return f"""# CHVD P1 — Neutral Randomized Pairwise Evaluation

Bạn là evaluator độc lập trong một thí nghiệm nghiên cứu về venture concepts.

Mỗi pair gồm Candidate A và Candidate B. Hai candidate có địa vị ngang nhau.
A/B đã được randomize. Không suy đoán candidate nào là bản gốc, bản chỉnh sửa,
được tạo ở temperature nào, hoặc thuộc method/pipeline nào.

Chỉ đánh giá nội dung trong hai candidate cards. Không dùng web, market data,
citation, kiến thức bên ngoài, hoặc assumptions về model.

## Cách trả lời

Với mỗi metric, chỉ chọn đúng một trong: `A`, `B`, hoặc `TIE`.

- `A`: Candidate A tốt hơn theo metric.
- `B`: Candidate B tốt hơn theo metric.
- `TIE`: Không có khác biệt đủ rõ chỉ từ nội dung card.

## Metrics

{metric_lines}

## Rules

1. Đánh giá từng pair độc lập; không so sánh các pair với nhau.
2. Không ưu ái candidate chỉ vì nghe practical, conventional, imaginative hoặc dài hơn.
3. Với risk metrics, chọn candidate có rủi ro THẤP hơn.
4. Với prototype_preference, chọn candidate nên được prototype/test trước khi nguồn lực hạn chế.
5. short_rationale tối đa 2 câu, nêu trade-off nội tại giữa A và B.
6. Trả đủ tất cả pair_id trong batch.
7. Chỉ trả valid JSON; không markdown và không prose ngoài JSON.

## Required JSON schema

```json
{json.dumps(schema, ensure_ascii=False, indent=2)}
```
"""


def create_pairs(
    payloads: Mapping[Tuple[str, str], Path],
    *,
    expected_domains: int,
    ideas_per_domain: int,
    max_chars: int,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    m1_domains = {domain for condition, domain in payloads if condition == "M1"}
    m3_domains = {domain for condition, domain in payloads if condition == "M3"}
    if m1_domains != m3_domains:
        raise ValueError(
            f"M1/M3 domains differ. Only M1: {sorted(m1_domains - m3_domains)}; "
            f"only M3: {sorted(m3_domains - m1_domains)}"
        )
    if expected_domains and len(m1_domains) != expected_domains:
        raise ValueError(f"Expected {expected_domains} domains, found {len(m1_domains)}: {sorted(m1_domains)}")

    pairs: List[Dict[str, Any]] = []
    manifest_rows: List[Dict[str, Any]] = []

    for domain in sorted(m1_domains):
        m1_path = payloads[("M1", domain)]
        m3_path = payloads[("M3", domain)]
        m1_doc = load_json(m1_path)
        m3_doc = load_json(m3_path)

        m1_cards: Dict[str, Dict[str, str]] = {}
        for idea in m1_doc["ideas"]:
            if not isinstance(idea, dict):
                raise ValueError(f"Non-object M1 idea in {m1_path}")
            card = make_m1_card(idea, domain, m1_path, max_chars)
            idea_id = card["_idea_id"]
            if idea_id in m1_cards:
                raise ValueError(f"Duplicate M1 idea_id {idea_id!r} in {m1_path}")
            m1_cards[idea_id] = card

        m3_items: Dict[str, Mapping[str, Any]] = {}
        for item in m3_doc["evaluations"]:
            if not isinstance(item, dict):
                raise ValueError(f"Non-object M3 evaluation in {m3_path}")
            idea_id = first_nonempty(item, "idea_id", "source_idea_id", "candidate_id")
            if not idea_id:
                raise ValueError(f"M3 item missing idea_id in {m3_path}")
            if idea_id in m3_items:
                raise ValueError(f"Duplicate M3 idea_id {idea_id!r} in {m3_path}")
            m3_items[idea_id] = item

        if set(m1_cards) != set(m3_items):
            raise ValueError(
                f"Unmatched idea IDs for domain {domain}. "
                f"M1-only={sorted(set(m1_cards)-set(m3_items))}; "
                f"M3-only={sorted(set(m3_items)-set(m1_cards))}"
            )
        if ideas_per_domain and len(m1_cards) != ideas_per_domain:
            raise ValueError(
                f"Expected {ideas_per_domain} matched ideas in domain {domain}, found {len(m1_cards)}."
            )

        for idea_id in sorted(m1_cards):
            m1_card = m1_cards[idea_id]
            m3_card = make_m3_card(m3_items[idea_id], m1_card, m3_path, max_chars)
            pairs.append({
                "_domain": domain,
                "_idea_id": idea_id,
                "_m1_card": m1_card,
                "_m3_card": m3_card,
            })

        manifest_rows.extend([
            {"role": "M1", "domain": domain, "path": str(m1_path.resolve()), "sha256": sha256_file(m1_path), "bytes": m1_path.stat().st_size},
            {"role": "M3", "domain": domain, "path": str(m3_path.resolve()), "sha256": sha256_file(m3_path), "bytes": m3_path.stat().st_size},
        ])

    expected_pairs = expected_domains * ideas_per_domain if expected_domains and ideas_per_domain else None
    if expected_pairs is not None and len(pairs) != expected_pairs:
        raise ValueError(f"Expected {expected_pairs} pairs, constructed {len(pairs)}")
    return pairs, manifest_rows


def prepare_output_dir(out_dir: Path, rebuild: bool) -> None:
    if out_dir.exists() and any(out_dir.iterdir()):
        if not rebuild:
            raise FileExistsError(
                f"Output directory is non-empty: {out_dir}\n"
                "Use a new --out-dir, or use --rebuild ONLY before any evaluation has started."
            )
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build P1 neutral randomized M1-vs-M3 pairwise batches for Gemini.")
    parser.add_argument("--input-root", required=True, help="Root containing M1_<domain>/ and M3_<domain>/ folders.")
    parser.add_argument("--out-dir", required=True, help="New output directory for public and private pairwise files.")
    parser.add_argument("--expected-domains", type=int, default=7, help="Set 0 to disable fixed domain-count validation.")
    parser.add_argument("--ideas-per-domain", type=int, default=5, help="Set 0 to disable fixed per-domain validation.")
    parser.add_argument("--batch-size", type=int, default=4, help="Pairs per public Gemini batch; 4 is quota-safe.")
    parser.add_argument("--seed", type=int, default=20260623, help="Frozen randomization seed recorded only in private manifest.")
    parser.add_argument("--max-field-chars", type=int, default=1300)
    parser.add_argument("--rebuild", action="store_true", help="Delete a non-empty output directory. Use only before judging begins.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.batch_size < 1:
        raise ValueError("--batch-size must be >= 1")
    if args.max_field_chars < 150:
        raise ValueError("--max-field-chars must be >= 150")

    input_root = Path(args.input_root).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    if not input_root.exists():
        raise FileNotFoundError(f"--input-root not found: {input_root}")

    payloads = discover_payloads(input_root)
    pairs, input_manifest = create_pairs(
        payloads,
        expected_domains=args.expected_domains,
        ideas_per_domain=args.ideas_per_domain,
        max_chars=args.max_field_chars,
    )

    rng = random.Random(args.seed)
    rng.shuffle(pairs)
    for idx, pair in enumerate(pairs, start=1):
        pair_id = f"P1PAIR_{idx:04d}"
        m1_is_a = bool(rng.randrange(2))
        pair["pair_id"] = pair_id
        pair["_m1_is_a"] = m1_is_a
        pair["candidate_a"] = public_card(pair["_m1_card"] if m1_is_a else pair["_m3_card"])
        pair["candidate_b"] = public_card(pair["_m3_card"] if m1_is_a else pair["_m1_card"])

    if args.dry_run:
        print("[DRY-RUN] Validation passed.")
        print(f"Matched M1→M3 pairs: {len(pairs)}")
        print(f"Public Gemini batches: {math.ceil(len(pairs) / args.batch_size)}")
        print("No files were created.")
        return

    prepare_output_dir(out_dir, args.rebuild)

    public_pairs = [
        {"pair_id": p["pair_id"], "candidate_a": p["candidate_a"], "candidate_b": p["candidate_b"]}
        for p in pairs
    ]
    for index, batch in enumerate(chunks(public_pairs, args.batch_size), start=1):
        write_json(
            out_dir / "public_gemini_batches" / f"pairwise_batch_{index:03d}.json",
            {
                "batch_id": f"P1_NEUTRAL_GEMINI_{index:03d}",
                "stage": "neutral_randomized_pairwise_m1_m3",
                "prompt_regime": PROMPT_REGIME,
                "pairs": batch,
            },
        )

    write_json(
        out_dir / "pairwise_neutral_output_schema.json",
        {
            "allowed_choice_values": list(CHOICES),
            "metrics": NEUTRAL_METRICS,
            "output_schema": {
                "evaluations": [{
                    "pair_id": "P1PAIR_0001",
                    "preferences": {metric["name"]: "A" for metric in NEUTRAL_METRICS},
                    "short_rationale": "",
                }]
            },
        },
    )
    (out_dir / "pairwise_neutral_prompt_vi.txt").write_text(neutral_prompt(), encoding="utf-8")

    private_key_rows = []
    directional_private_rows = []
    for p in pairs:
        candidate_a_method = M1_METHOD if p["_m1_is_a"] else M3_METHOD
        candidate_b_method = M3_METHOD if p["_m1_is_a"] else M1_METHOD
        private_key_rows.append({
            "pair_id": p["pair_id"],
            "prompt_regime": PROMPT_REGIME,
            "domain": p["_domain"],
            "source_idea_id": p["_idea_id"],
            "candidate_a_method": candidate_a_method,
            "candidate_b_method": candidate_b_method,
            "candidate_a_source_file": (p["_m1_card"] if p["_m1_is_a"] else p["_m3_card"])["_source_file"],
            "candidate_b_source_file": (p["_m3_card"] if p["_m1_is_a"] else p["_m1_card"])["_source_file"],
        })
        directional_private_rows.append({
            "pair_id": p["pair_id"],
            "source_candidate": public_card(p["_m1_card"]),
            "practical_revision": public_card(p["_m3_card"]),
        })

    write_csv(
        out_dir / "pairwise_key_private.csv",
        private_key_rows,
        (
            "pair_id", "prompt_regime", "domain", "source_idea_id",
            "candidate_a_method", "candidate_b_method",
            "candidate_a_source_file", "candidate_b_source_file",
        ),
    )
    write_jsonl(out_dir / "private_directional_traceability_input.jsonl", directional_private_rows)
    write_csv(out_dir / "input_manifest_private_sha256.csv", input_manifest, ("role", "domain", "path", "sha256", "bytes"))

    public_manifest = {
        "created_at_utc": utc_now(),
        "prompt_regime": PROMPT_REGIME,
        "pair_design": "Matched M1-vs-M3; A/B order randomized and method labels withheld.",
        "pair_count": len(pairs),
        "public_batch_size": args.batch_size,
        "public_batch_count": math.ceil(len(pairs) / args.batch_size),
        "neutral_metrics": [m["name"] for m in NEUTRAL_METRICS],
        "privacy_notice": "No method labels, source labels, seed, or A/B mapping appear in public files.",
    }
    write_json(out_dir / "public_manifest.json", public_manifest)
    write_json(
        out_dir / "manifest_private.json",
        {
            **public_manifest,
            "seed": args.seed,
            "input_root": str(input_root),
            "private_pair_key": "pairwise_key_private.csv",
            "private_directional_audit_input": "private_directional_traceability_input.jsonl",
            "input_sha256_manifest": "input_manifest_private_sha256.csv",
        },
    )

    (out_dir / "README_PRIVATE_WARNING.md").write_text(
        """# PRIVATE FILE WARNING

Never upload, paste, or share these files with Gemini, another LLM, or human evaluators:

- `pairwise_key_private.csv`
- `private_directional_traceability_input.jsonl`
- `input_manifest_private_sha256.csv`
- `manifest_private.json`

These files reveal the M1/M3 mapping or source/revision direction.

Public Gemini files are only:

- `public_gemini_batches/`
- `pairwise_neutral_prompt_vi.txt`
- `pairwise_neutral_output_schema.json`
- `public_manifest.json`
""",
        encoding="utf-8",
    )
    (out_dir / "README_PUBLIC.md").write_text(
        f"""# CHVD P1 Neutral Pairwise Public Package

- Pair count: {len(pairs)}
- Protocol: matched M1-vs-M3 pairs, randomized A/B, blinded method labels.
- Use only the files in `public_gemini_batches/` together with
  `pairwise_neutral_prompt_vi.txt` and `pairwise_neutral_output_schema.json`.

Do not infer source/revision status, model, temperature, or hidden methods.
Return A, B, or TIE for every metric in every pair.
""",
        encoding="utf-8",
    )

    print("[OK] P1 neutral shuffled/blind pairwise package created.")
    print(f"[OK] Matched pairs: {len(pairs)}")
    print(f"[OK] Gemini public batches: {math.ceil(len(pairs) / args.batch_size)}")
    print(f"[OK] Output: {out_dir}")
    print("[WARNING] Never upload any filename containing 'private' to Gemini.")


if __name__ == "__main__":
    main()
