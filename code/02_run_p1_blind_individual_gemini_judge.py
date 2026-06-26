#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CHVD P1 — Step 2: Blind individual metric evaluation with Gemini API.

Input:
  A public batches directory created by 01_prepare_p1_blind_individual_dataset.py

Output:
  - batch_scores/batch_XXX.scores.json
  - raw_debug/batch_XXX.raw.txt
  - blind_individual_judge_scores.jsonl
  - blind_individual_judge_scores.json
  - judge_manifest.json

This script sends only anonymous candidate cards to Gemini.
It never reads a private blind key and cannot unblind methods by itself.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

try:
    from google import genai
    from google.genai import types
except ImportError:
    genai = None
    types = None

METRICS = [
    "novelty",
    "conceptual_coherence",
    "user_pain_plausibility",
    "technical_plausibility",
    "mvp_clarity",
    "business_model_plausibility",
    "unsupported_claim_risk",
    "ethical_privacy_risk",
    "overall_mvp_usefulness",
    "prototype_willingness",
]
DAILY_QUOTA_MARKERS = (
    "GenerateRequestsPerDay",
    "RequestsPerDay",
    "quotaValue",
    "PerDay",
)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"Cannot read JSON: {path}: {exc}") from exc


def load_batches(batch_dir: Path) -> List[Path]:
    paths = sorted(batch_dir.glob("batch_*.json"))
    if not paths:
        raise FileNotFoundError(f"No batch_*.json files found in: {batch_dir}")
    return paths


def extract_json_object(text: str) -> Dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.I)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        value = json.loads(cleaned)
        if isinstance(value, dict):
            return value
    except Exception:
        pass

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end > start:
        value = json.loads(cleaned[start:end + 1])
        if isinstance(value, dict):
            return value
    raise ValueError("Gemini response does not contain a valid JSON object.")


def validate_score(value: Any, metric: str) -> float:
    try:
        score = float(value)
    except Exception as exc:
        raise ValueError(f"Metric {metric!r} is not numeric: {value!r}") from exc
    if not 1.0 <= score <= 5.0:
        raise ValueError(f"Metric {metric!r} must be in [1.0, 5.0], got {score}.")
    return score


def normalize_evaluations(payload: Dict[str, Any], expected_ids: Sequence[str]) -> List[Dict[str, Any]]:
    evaluations = payload.get("evaluations")
    if not isinstance(evaluations, list):
        raise ValueError("Missing top-level 'evaluations' list.")

    expected = set(expected_ids)
    seen = set()
    normalized: List[Dict[str, Any]] = []

    for item in evaluations:
        if not isinstance(item, dict):
            raise ValueError("Every evaluation must be an object.")
        candidate_id = str(item.get("candidate_id", "")).strip()
        if candidate_id not in expected:
            raise ValueError(f"Unexpected candidate_id: {candidate_id!r}")
        if candidate_id in seen:
            raise ValueError(f"Duplicate candidate_id: {candidate_id!r}")
        seen.add(candidate_id)

        scores = item.get("scores")
        if not isinstance(scores, dict):
            raise ValueError(f"Missing score object for {candidate_id}")

        normalized_scores = {metric: validate_score(scores.get(metric), metric) for metric in METRICS}
        normalized.append({
            "candidate_id": candidate_id,
            "scores": normalized_scores,
            "short_rationale": str(item.get("short_rationale", "")).strip()[:700],
        })

    missing = expected - seen
    if missing:
        raise ValueError(f"Gemini omitted {len(missing)} candidate IDs: {sorted(missing)}")
    if len(normalized) != len(expected_ids):
        raise ValueError("Evaluation count does not match batch size.")

    return sorted(normalized, key=lambda item: item["candidate_id"])


def build_prompt(cards: List[Dict[str, Any]]) -> str:
    cards_json = json.dumps(cards, ensure_ascii=False, indent=2)
    return f"""
You are an independent blinded evaluator for a research study on LLM-generated venture concepts.

You will receive multiple anonymous candidate cards. Evaluate every card independently.
Do not compare candidates with one another. Do not infer the generation method, prompt regime,
temperature, model, ordering, or any hidden label. Do not use web search, citations, market
statistics, company knowledge, patents, or external factual claims. Judge only the information
explicitly contained in each candidate card.

Use a continuous numeric score from 1.0 to 5.0 for each metric. One decimal place is preferred.
Do not use a score below 1.0 or above 5.0.

Higher-is-better metrics:
- novelty: distinctiveness and non-generic creative mechanism.
- conceptual_coherence: internal logic from problem to concept, approach, and prototype.
- user_pain_plausibility: whether the stated user problem or desire is plausible as a hypothesis.
- technical_plausibility: whether the described prototype is feasible within its stated scope.
- mvp_clarity: whether an initial MVP is narrow, concrete, and testable.
- business_model_plausibility: whether a value-exchange or monetization hypothesis is plausible.
- overall_mvp_usefulness: usefulness as a candidate MVP to test.
- prototype_willingness: willingness to prioritize this candidate for an early prototype.

Lower-is-better metrics:
- unsupported_claim_risk: reliance on vague, unverified, overly broad, or unsupported claims.
- ethical_privacy_risk: risks involving privacy, sensitive data, manipulation, discrimination, or misuse.

Rules:
1. Do not reward a candidate merely for sounding imaginative.
2. Do not reward a candidate merely for sounding conventional or practical.
3. Do not invent missing facts, market data, or technical capabilities.
4. Do not rewrite, improve, critique outside the rubric, or merge ideas.
5. A speculative idea is not automatically bad; penalize it only when unsupported claims prevent a clear bounded MVP.
6. Keep each rationale to at most two short sentences.
7. Return every candidate_id exactly once.
8. Return valid JSON only. Do not use Markdown or code fences.

Required JSON output:
{{
  "evaluations": [
    {{
      "candidate_id": "BLIND_0001",
      "scores": {{
        "novelty": 1.0,
        "conceptual_coherence": 1.0,
        "user_pain_plausibility": 1.0,
        "technical_plausibility": 1.0,
        "mvp_clarity": 1.0,
        "business_model_plausibility": 1.0,
        "unsupported_claim_risk": 1.0,
        "ethical_privacy_risk": 1.0,
        "overall_mvp_usefulness": 1.0,
        "prototype_willingness": 1.0
      }},
      "short_rationale": ""
    }}
  ]
}}

CANDIDATE CARDS:
{cards_json}
""".strip()


def daily_quota_error(message: str) -> bool:
    return any(marker.lower() in message.lower() for marker in DAILY_QUOTA_MARKERS)


def evaluate_batch(
    *,
    client: Any,
    model: str,
    cards: List[Dict[str, Any]],
    max_output_tokens: int,
    retries: int,
    retry_sleep_base: float,
) -> tuple[List[Dict[str, Any]], str]:
    prompt = build_prompt(cards)
    expected_ids = [str(card["candidate_id"]) for card in cards]
    raw_text = ""
    last_error: Exception | None = None

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
            return normalize_evaluations(parsed, expected_ids), raw_text

        except KeyboardInterrupt:
            raise
        except Exception as exc:
            last_error = exc
            message = str(exc)
            if daily_quota_error(message):
                raise RuntimeError(
                    "Gemini daily quota appears exhausted. The script stops without retrying. "
                    "Wait for quota reset, then rerun without --force so completed batches are preserved."
                ) from exc
            print(f"[WARN] Batch attempt {attempt}/{retries} failed: {message[:350]}")
            if attempt < retries:
                wait_seconds = min(45.0, retry_sleep_base * (2 ** (attempt - 1)))
                print(f"[WARN] Waiting {wait_seconds:.1f}s before retry...")
                time.sleep(wait_seconds)

    raise RuntimeError(
        f"Batch failed after {retries} attempts: {last_error}\n"
        f"Raw response excerpt: {raw_text[:1000]}"
    )


def existing_valid_output(score_path: Path, expected_ids: Sequence[str]) -> bool:
    if not score_path.exists():
        return False
    try:
        payload = load_json(score_path)
        normalize_evaluations(payload, expected_ids)
        return True
    except Exception:
        return False


def write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="P1 blind individual evaluation with Gemini 2.5 Flash Lite.")
    parser.add_argument("--batch-dir", required=True, help="Public batches folder from Step 1.")
    parser.add_argument("--out-dir", required=True, help="New or resumable judge output folder.")
    parser.add_argument("--model", default="gemini-2.5-flash-lite")
    parser.add_argument("--api-key-env", default="GEMINI_JUDGE_KEY")
    parser.add_argument("--max-output-tokens", type=int, default=7000)
    parser.add_argument("--retries", type=int, default=4)
    parser.add_argument("--retry-sleep-base", type=float, default=2.5)
    parser.add_argument("--rerun", action="store_true", help="Rerun even valid completed batches. Avoid after freezing results.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if genai is None or types is None:
        raise RuntimeError("Missing google-genai. Install with: py -m pip install -U google-genai")

    batch_dir = Path(args.batch_dir).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    batch_paths = load_batches(batch_dir)

    if args.dry_run:
        total = 0
        for batch_path in batch_paths:
            cards = load_json(batch_path)
            if not isinstance(cards, list) or not cards:
                raise ValueError(f"Invalid empty batch: {batch_path}")
            total += len(cards)
            print(f"[DRY-RUN] {batch_path.name}: {len(cards)} candidates")
        print(f"[DRY-RUN] total candidates={total}; calls={len(batch_paths)}")
        return

    api_key = os.environ.get(args.api_key_env, "").strip()
    if not api_key:
        raise RuntimeError(
            f"Missing API key. PowerShell exampl[LOCAL_PATH_REDACTED]\"YOUR_API_KEY\""
        )

    score_dir = out_dir / "batch_scores"
    raw_dir = out_dir / "raw_debug"
    score_dir.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)

    client = genai.Client(api_key=api_key)
    all_records: List[Dict[str, Any]] = []
    batch_manifest: List[Dict[str, Any]] = []

    for index, batch_path in enumerate(batch_paths, start=1):
        cards = load_json(batch_path)
        if not isinstance(cards, list) or not cards:
            raise ValueError(f"Batch must be a non-empty list: {batch_path}")
        expected_ids = [str(card.get("candidate_id", "")).strip() for card in cards]
        if any(not candidate_id for candidate_id in expected_ids):
            raise ValueError(f"Missing candidate_id in {batch_path}")
        if len(set(expected_ids)) != len(expected_ids):
            raise ValueError(f"Duplicate candidate_id in {batch_path}")

        score_path = score_dir / f"{batch_path.stem}.scores.json"
        raw_path = raw_dir / f"{batch_path.stem}.raw.txt"

        if not args.rerun and existing_valid_output(score_path, expected_ids):
            payload = load_json(score_path)
            evaluations = normalize_evaluations(payload, expected_ids)
            print(f"[SKIP] {batch_path.name}: valid output already exists")
        else:
            print(f"[INFO] Evaluating {index}/{len(batch_paths)}: {batch_path.name} ({len(cards)} candidates)")
            evaluations, raw_text = evaluate_batch(
                client=client,
                model=args.model,
                cards=cards,
                max_output_tokens=args.max_output_tokens,
                retries=args.retries,
                retry_sleep_base=args.retry_sleep_base,
            )
            raw_path.write_text(raw_text, encoding="utf-8")
            payload = {
                "metadata": {
                    "stage": "P1_blind_individual_metric_judge",
                    "judge_model": args.model,
                    "temperature": 0.0,
                    "top_p": 0.1,
                    "score_scale": "continuous_numeric_1_to_5",
                    "batch_source": str(batch_path.resolve()),
                    "batch_source_sha256": sha256_file(batch_path),
                    "created_at_utc": now_iso(),
                },
                "evaluations": evaluations,
            }
            score_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[OK] {score_path}")

        for evaluation in evaluations:
            all_records.append({
                **evaluation,
                "judge_model": args.model,
                "batch_id": batch_path.stem,
            })
        batch_manifest.append({
            "batch_id": batch_path.stem,
            "batch_path": str(batch_path.resolve()),
            "batch_sha256": sha256_file(batch_path),
            "score_path": str(score_path.resolve()),
            "score_sha256": sha256_file(score_path),
            "raw_debug_path": str(raw_path.resolve()) if raw_path.exists() else "",
            "candidate_count": len(expected_ids),
        })

    if len({record["candidate_id"] for record in all_records}) != len(all_records):
        raise RuntimeError("Duplicate candidate_id found across combined judge output.")

    all_records.sort(key=lambda record: record["candidate_id"])
    write_jsonl(out_dir / "blind_individual_judge_scores.jsonl", all_records)
    (out_dir / "blind_individual_judge_scores.json").write_text(
        json.dumps({"evaluations": all_records}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out_dir / "batch_output_manifest.json").write_text(
        json.dumps(batch_manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out_dir / "judge_manifest.json").write_text(
        json.dumps({
            "stage": "P1_blind_individual_metric_judge",
            "prompt_regime": "P1_shared_neutral",
            "judge_model": args.model,
            "temperature": 0.0,
            "top_p": 0.1,
            "score_scale": "continuous_numeric_1_to_5",
            "metrics": METRICS,
            "batch_count": len(batch_paths),
            "candidate_count": len(all_records),
            "created_at_utc": now_iso(),
            "note": "The judge receives only anonymous public cards. Private blind keys are not read by this script.",
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("[OK] P1 Gemini judging complete")
    print(f"  candidates: {len(all_records)}")
    print(f"  combined JSONL: {(out_dir / 'blind_individual_judge_scores.jsonl').resolve()}")
    print(f"  combined JSON:  {(out_dir / 'blind_individual_judge_scores.json').resolve()}")


if __name__ == "__main__":
    main()
