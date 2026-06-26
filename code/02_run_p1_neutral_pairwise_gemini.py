#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CHVD P1 — Run Gemini API for Neutral Randomized Pairwise Judging
=================================================================

This is the PRIMARY P1 pairwise judge runner.

Input:
    public_gemini_batches/pairwise_batch_XXX.json
created by:
    01_build_p1_neutral_pairwise_blind.py

The Gemini judge sees only Candidate A and Candidate B in random order.
It never receives M0/M1/M3 labels, source/revision labels, temperatures,
private mapping files, or generation prompts.

Output:
    <out-dir>/batch_scores/pairwise_batch_XXX.scores.json
    <out-dir>/pairwise_neutral_gemini_scores.jsonl
    <out-dir>/pairwise_neutral_gemini_manifest.json
    <out-dir>/raw_debug/*.raw.txt

Install:
    py -m pip install -U google-genai

PowerShell:
    $env:GEMINI_PAIRWISE_JUDGE_KEY="YOUR_KEY"
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence

try:
    from google import genai
    from google.genai import types
except ImportError:
    genai = None
    types = None


METRICS = [
    "novelty_preference",
    "conceptual_coherence_preference",
    "user_pain_plausibility_preference",
    "technical_plausibility_preference",
    "mvp_clarity_preference",
    "business_model_plausibility_preference",
    "lower_unsupported_claim_risk",
    "lower_ethical_privacy_risk",
    "overall_mvp_usefulness_preference",
    "prototype_preference",
]
VALID_CHOICES = {"A", "B", "TIE"}
FORBIDDEN_PRIVATE_TERMS = (
    "candidate_a_method",
    "candidate_b_method",
    "source_candidate",
    "practical_revision",
    "M1_RAW_HIGH_TEMP",
    "M3_CHVD_DISTILLED",
)


def now_iso() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"Cannot parse JSON: {path}: {exc}") from exc


def public_path_guard(path: Path) -> None:
    lowered = str(path).lower()
    if "private" in lowered:
        raise RuntimeError(f"Refusing private-path input: {path}")
    raw = path.read_text(encoding="utf-8")
    for term in FORBIDDEN_PRIVATE_TERMS:
        if term in raw:
            raise RuntimeError(
                f"Refusing input because it contains a private/directional term {term!r}: {path}"
            )


def load_public_batch(path: Path) -> tuple[str, List[Dict[str, Any]]]:
    public_path_guard(path)
    payload = load_json(path)
    if not isinstance(payload, dict):
        raise ValueError(f"Batch must be an object: {path}")
    batch_id = str(payload.get("batch_id", "")).strip()
    pairs = payload.get("pairs")
    if not batch_id or not isinstance(pairs, list) or not pairs:
        raise ValueError(f"Invalid batch structure: {path}")
    seen = set()
    clean_pairs = []
    for pair in pairs:
        if not isinstance(pair, dict):
            raise ValueError(f"Non-object pair in {path}")
        pair_id = str(pair.get("pair_id", "")).strip()
        a, b = pair.get("candidate_a"), pair.get("candidate_b")
        if not pair_id or pair_id in seen or not isinstance(a, dict) or not isinstance(b, dict):
            raise ValueError(f"Invalid pair in {path}: {pair_id!r}")
        seen.add(pair_id)
        clean_pairs.append({"pair_id": pair_id, "candidate_a": a, "candidate_b": b})
    return batch_id, clean_pairs


def strip_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def extract_json_object(text: str) -> Dict[str, Any]:
    cleaned = strip_fences(text)
    try:
        value = json.loads(cleaned)
        if isinstance(value, dict):
            return value
    except Exception:
        pass
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start >= 0 and end > start:
        value = json.loads(cleaned[start:end + 1])
        if isinstance(value, dict):
            return value
    raise ValueError("Gemini output does not contain a valid JSON object.")


def normalize_output(payload: Mapping[str, Any], expected_pair_ids: Sequence[str]) -> List[Dict[str, Any]]:
    evaluations = payload.get("evaluations")
    if not isinstance(evaluations, list):
        raise ValueError("Missing top-level evaluations list.")
    expected = set(expected_pair_ids)
    seen = set()
    normalized = []

    for item in evaluations:
        if not isinstance(item, dict):
            raise ValueError("Each evaluation must be an object.")
        pair_id = str(item.get("pair_id", "")).strip()
        if pair_id not in expected:
            raise ValueError(f"Unexpected pair_id: {pair_id!r}")
        if pair_id in seen:
            raise ValueError(f"Duplicate pair_id: {pair_id!r}")
        seen.add(pair_id)

        prefs = item.get("preferences")
        if not isinstance(prefs, dict):
            raise ValueError(f"Missing preferences object for {pair_id}")
        normalized_prefs = {}
        for metric in METRICS:
            value = str(prefs.get(metric, "")).strip().upper()
            if value not in VALID_CHOICES:
                raise ValueError(f"Invalid choice for {pair_id}/{metric}: {value!r}")
            normalized_prefs[metric] = value

        normalized.append({
            "pair_id": pair_id,
            "preferences": normalized_prefs,
            "short_rationale": str(item.get("short_rationale", "")).strip()[:900],
        })

    missing = expected - seen
    if missing:
        raise ValueError(f"Gemini omitted pair IDs: {sorted(missing)}")
    if len(normalized) != len(expected_pair_ids):
        raise ValueError("Evaluation count does not match expected pair count.")
    return sorted(normalized, key=lambda x: x["pair_id"])


def build_prompt(pairs: List[Dict[str, Any]]) -> str:
    pairs_json = json.dumps(pairs, ensure_ascii=False, indent=2)
    return f"""
Bạn là evaluator độc lập, chấm mù cho một nghiên cứu về venture concepts.

Mỗi pair có Candidate A và Candidate B. Hai candidate có địa vị ngang nhau.
Không suy đoán candidate nào đến từ method nào, bản gốc/bản chỉnh sửa, model,
temperature, hoặc prompt regime nào. Chỉ dựa vào nội dung trong pair.

Không dùng web, citations, market data, company knowledge hoặc facts bên ngoài.
Không rewrite, merge, cải thiện hoặc thêm ý tưởng mới.

Với mỗi metric, chỉ trả `A`, `B`, hoặc `TIE`.

Higher-is-better:
- novelty_preference: candidate nào có cơ chế/approach khác biệt và sáng tạo hơn?
- conceptual_coherence_preference: candidate nào logic hơn từ problem đến MVP?
- user_pain_plausibility_preference: candidate nào có user pain/desire hợp lý hơn?
- technical_plausibility_preference: candidate nào khả thi kỹ thuật hơn trong scope?
- mvp_clarity_preference: candidate nào có MVP cụ thể, hẹp và testable hơn?
- business_model_plausibility_preference: candidate nào có value exchange/business model hợp lý hơn?
- overall_mvp_usefulness_preference: candidate nào hữu ích hơn như MVP hypothesis?
- prototype_preference: candidate nào nên được prototype/test trước?

Lower-is-better:
- lower_unsupported_claim_risk: candidate nào có ít unsupported claim/overclaim hơn?
- lower_ethical_privacy_risk: candidate nào có risk ethics/privacy/misuse thấp hơn?

Rules:
1. Với risk metrics, chọn candidate có rủi ro THẤP hơn.
2. Không ưu ái candidate vì dài hơn, conventional hơn, hoặc imaginative hơn.
3. Dùng TIE khi card không cung cấp bằng chứng đủ rõ.
4. Rationale tối đa 2 câu, mô tả trade-off giữa A/B.
5. Return every pair_id exactly once.
6. Return JSON only. No Markdown, no code fence, no extra prose.

Required output:
{{
  "evaluations": [
    {{
      "pair_id": "P1PAIR_0001",
      "preferences": {{
        "novelty_preference": "A",
        "conceptual_coherence_preference": "A",
        "user_pain_plausibility_preference": "A",
        "technical_plausibility_preference": "A",
        "mvp_clarity_preference": "A",
        "business_model_plausibility_preference": "A",
        "lower_unsupported_claim_risk": "A",
        "lower_ethical_privacy_risk": "A",
        "overall_mvp_usefulness_preference": "A",
        "prototype_preference": "A"
      }},
      "short_rationale": ""
    }}
  ]
}}

PAIR RECORDS:
{pairs_json}
""".strip()


def is_daily_quota_error(message: str) -> bool:
    markers = (
        "GenerateRequestsPerDayPerProjectPerModel-FreeTier",
        "RequestsPerDay",
        "quotaValue",
        "Resource has been exhausted",
    )
    return any(marker.lower() in message.lower() for marker in markers)


def evaluate_batch(
    *,
    client: Any,
    model: str,
    pairs: List[Dict[str, Any]],
    max_output_tokens: int,
    retries: int,
    retry_sleep_base: float,
) -> tuple[List[Dict[str, Any]], str]:
    prompt = build_prompt(pairs)
    expected_ids = [pair["pair_id"] for pair in pairs]
    last_error: Exception | None = None
    raw_text = ""

    for attempt in range(1, retries + 1):
        try:
            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.0,
                    top_p=0.1,
                    max_output_tokens=max_output_tokens,
                    response_mime_type="application/json",
                ),
            )
            raw_text = response.text or ""
            parsed = extract_json_object(raw_text)
            return normalize_output(parsed, expected_ids), raw_text
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            last_error = exc
            msg = str(exc)
            if is_daily_quota_error(msg):
                raise RuntimeError(
                    "Gemini daily quota appears exhausted. Stopping without retry. "
                    "Wait for reset and rerun without --rerun so valid batches are preserved."
                ) from exc
            print(f"[WARN] {attempt}/{retries} failed: {msg[:400]}")
            if attempt < retries:
                wait = min(60.0, retry_sleep_base * (2 ** (attempt - 1)))
                print(f"[WARN] Waiting {wait:.1f}s before retry...")
                time.sleep(wait)

    raise RuntimeError(f"Batch failed after {retries} attempts: {last_error}\nRaw excerpt: {raw_text[:900]}")


def valid_existing_score(path: Path, expected_pair_ids: Sequence[str]) -> bool:
    if not path.exists():
        return False
    try:
        payload = load_json(path)
        normalize_output(payload, expected_pair_ids)
        return True
    except Exception:
        return False


def write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run blinded neutral P1 M1-vs-M3 pairwise Gemini judging.")
    parser.add_argument("--batch-dir", required=True, help="PUBLIC public_gemini_batches folder from Script 01.")
    parser.add_argument("--out-dir", required=True, help="Output folder for resumable Gemini pairwise scores.")
    parser.add_argument("--model", default="gemini-2.5-flash-lite")
    parser.add_argument("--api-key-env", default="GEMINI_PAIRWISE_JUDGE_KEY")
    parser.add_argument("--max-output-tokens", type=int, default=7500)
    parser.add_argument("--retries", type=int, default=4)
    parser.add_argument("--retry-sleep-base", type=float, default=2.5)
    parser.add_argument("--rerun", action="store_true", help="Rerun valid completed batches. Avoid after freezing results.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if genai is None or types is None:
        raise RuntimeError("Missing google-genai. Install: py -m pip install -U google-genai")

    batch_dir = Path(args.batch_dir).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    if "private" in str(batch_dir).lower():
        raise RuntimeError("Refusing a private batch directory.")
    batch_paths = sorted(batch_dir.glob("pairwise_batch_*.json"))
    if not batch_paths:
        raise FileNotFoundError(f"No pairwise_batch_*.json under: {batch_dir}")

    loaded = []
    for path in batch_paths:
        batch_id, pairs = load_public_batch(path)
        loaded.append((path, batch_id, pairs))
        if args.dry_run:
            print(f"[DRY-RUN] {path.name}: {len(pairs)} blinded pairs")
    if args.dry_run:
        print(f"[DRY-RUN] Total pairs: {sum(len(x[2]) for x in loaded)}")
        return

    api_key = os.environ.get(args.api_key_env, "").strip()
    if not api_key:
        raise RuntimeError(f"Missing API key. PowerShell: $env:{args.api_key_env}='YOUR_KEY'")

    score_dir = out_dir / "batch_scores"
    debug_dir = out_dir / "raw_debug"
    score_dir.mkdir(parents=True, exist_ok=True)
    debug_dir.mkdir(parents=True, exist_ok=True)
    client = genai.Client(api_key=api_key)
    all_records = []

    manifest_inputs = []
    for index, (path, batch_id, pairs) in enumerate(loaded, start=1):
        output_path = score_dir / f"{path.stem}.scores.json"
        expected_ids = [pair["pair_id"] for pair in pairs]
        manifest_inputs.append({"batch_file": path.name, "sha256": sha256_file(path), "pairs": len(pairs), "batch_id": batch_id})

        if not args.rerun and valid_existing_score(output_path, expected_ids):
            records = normalize_output(load_json(output_path), expected_ids)
            print(f"[SKIP] {path.name}: valid score already exists")
        else:
            print(f"[INFO] {index}/{len(loaded)} {path.name}: {len(pairs)} blinded pairs")
            records, raw_text = evaluate_batch(
                client=client,
                model=args.model,
                pairs=pairs,
                max_output_tokens=args.max_output_tokens,
                retries=args.retries,
                retry_sleep_base=args.retry_sleep_base,
            )
            (debug_dir / f"{path.stem}.raw.txt").write_text(raw_text, encoding="utf-8")
            payload = {
                "metadata": {
                    "stage": "neutral_randomized_pairwise_m1_m3_judge",
                    "judge_model": args.model,
                    "temperature": 0.0,
                    "top_p": 0.1,
                    "batch_id": batch_id,
                    "input_source": str(path),
                    "created_at": now_iso(),
                    "blindness_note": "The judge received randomized Candidate A/B cards without method or source/revision labels.",
                },
                "evaluations": records,
            }
            output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[OK] {output_path}")

        for record in records:
            all_records.append({**record, "batch_file": path.name, "batch_id": batch_id, "judge_model": args.model})

    all_records.sort(key=lambda x: x["pair_id"])
    combined = out_dir / "pairwise_neutral_gemini_scores.jsonl"
    write_jsonl(combined, all_records)
    (out_dir / "input_public_batch_sha256.json").write_text(
        json.dumps(manifest_inputs, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    manifest = {
        "stage": "neutral_randomized_pairwise_m1_m3_judge",
        "judge_model": args.model,
        "temperature": 0.0,
        "top_p": 0.1,
        "metrics": METRICS,
        "allowed_choice_values": sorted(VALID_CHOICES),
        "num_batches": len(loaded),
        "num_pairs": sum(len(x[2]) for x in loaded),
        "combined_output": str(combined.resolve()),
        "created_at": now_iso(),
        "notes": [
            "Neutral A/B randomized, blind M1-vs-M3 pairwise protocol.",
            "This output contains no M1/M3 mapping and can be shared only with the local unblinding script.",
            "Do not merge with directional P0-style audit scores.",
        ],
    }
    (out_dir / "pairwise_neutral_gemini_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[OK] Combined output: {combined.resolve()}")


if __name__ == "__main__":
    main()
