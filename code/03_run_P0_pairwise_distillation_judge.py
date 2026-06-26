#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CHVD Blind Evaluation Toolkit — Step 3

Run a directional pairwise judge for M1 raw high-temperature ideas and their
M3 CHVD-distilled counterparts prepared by 01_shuffle_merge_blind_dataset.py.

Input:
    pairwise_distillation_input.jsonl

Output:
    <out-dir>/pairwise_batch_scores/pairwise_batch_XXX.scores.json
    <out-dir>/pairwise_distillation_judge_scores.jsonl
    <out-dir>/pairwise_judge_manifest.json
    <out-dir>/raw_debug/*.raw.txt

The judge sees only a "Source candidate" and its "Practical revision". It does
not receive M0/M1/M3 labels, temperatures, generator prompts, or private keys.

Requires:
    pip install -U google-genai
    $env:GEMINI_JUDGE_KEY="YOUR_KEY"
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

try:
    from google import genai
    from google.genai import types
except ImportError:
    genai = None
    types = None


PAIR_METRICS = [
    "creative_core_preservation",
    "novelty_retention",
    "practical_mvp_improvement",
    "overclaim_removal_quality",
    "semantic_drift_risk",
]
PREFERENCE_VALUES = {"A", "B", "TIE"}


def now_iso() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"JSONL lỗi ở dòng {line_no}: {exc}") from exc
            if not isinstance(record, dict):
                raise ValueError(f"JSONL dòng {line_no} phải là object.")
            records.append(record)
    if not records:
        raise ValueError(f"Không có pair record trong: {path}")
    return records


def validate_pair_record(record: Dict[str, Any]) -> None:
    pair_id = str(record.get("pair_id", "")).strip()
    if not pair_id:
        raise ValueError("Pair record thiếu pair_id.")
    for field in ("version_a", "version_b"):
        value = record.get(field)
        if not isinstance(value, dict):
            raise ValueError(f"Pair {pair_id} thiếu object '{field}'.")
        candidate_id = str(value.get("candidate_id", "")).strip()
        if not candidate_id:
            raise ValueError(f"Pair {pair_id}, {field} thiếu candidate_id.")


def chunked(values: Sequence[Dict[str, Any]], size: int) -> Iterable[List[Dict[str, Any]]]:
    for start in range(0, len(values), size):
        yield list(values[start : start + size])


def strip_json_fence(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def parse_json_object(text: str) -> Dict[str, Any]:
    cleaned = strip_json_fence(text)
    try:
        payload = json.loads(cleaned)
    except Exception as exc:
        raise ValueError(f"Judge trả JSON lỗi: {exc}") from exc
    if isinstance(payload, list):
        payload = {"evaluations": payload}
    if not isinstance(payload, dict):
        raise ValueError("Judge output phải là JSON object hoặc JSON list.")
    return payload


def clamp_score(value: Any, metric: str) -> float:
    try:
        score = float(value)
    except Exception as exc:
        raise ValueError(f"Metric '{metric}' không phải số: {value!r}") from exc
    if score < 1.0 or score > 5.0:
        raise ValueError(f"Metric '{metric}' phải trong [1, 5], nhận {score}.")
    return round(score, 2)


def normalize_evaluations(payload: Dict[str, Any], expected_pair_ids: Sequence[str]) -> List[Dict[str, Any]]:
    evaluations = payload.get("evaluations", payload.get("results"))
    if not isinstance(evaluations, list):
        raise ValueError("Judge output thiếu list 'evaluations'.")

    expected = set(expected_pair_ids)
    received: set[str] = set()
    normalized: List[Dict[str, Any]] = []

    for item in evaluations:
        if not isinstance(item, dict):
            raise ValueError("Mỗi pairwise evaluation phải là JSON object.")
        pair_id = str(item.get("pair_id", "")).strip()
        if pair_id not in expected:
            raise ValueError(f"pair_id không thuộc batch: {pair_id!r}")
        if pair_id in received:
            raise ValueError(f"pair_id bị trùng trong judge output: {pair_id}")
        received.add(pair_id)

        scores = item.get("scores")
        if not isinstance(scores, dict):
            raise ValueError(f"Pair {pair_id} thiếu scores.")
        clean_scores = {metric: clamp_score(scores.get(metric), metric) for metric in PAIR_METRICS}

        preference = str(item.get("preferred_for_prototype", "")).strip().upper()
        if preference not in PREFERENCE_VALUES:
            raise ValueError(
                f"Pair {pair_id}: preferred_for_prototype phải là A, B hoặc TIE; nhận {preference!r}."
            )

        rationale = str(item.get("short_rationale", "")).strip()
        normalized.append({
            "pair_id": pair_id,
            "scores": clean_scores,
            "preferred_for_prototype": preference,
            "short_rationale": rationale[:900],
        })

    missing = expected - received
    if missing:
        raise ValueError(f"Judge thiếu {len(missing)} pair_id: {sorted(missing)}")
    if len(normalized) != len(expected_pair_ids):
        raise ValueError("Số pairwise evaluation không khớp số pair trong batch.")
    return normalized


def public_pair_for_prompt(pair: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "pair_id": pair["pair_id"],
        "source_candidate": pair["version_a"],
        "practical_revision": pair["version_b"],
    }


def build_prompt(pairs: List[Dict[str, Any]]) -> str:
    pairs_json = json.dumps([public_pair_for_prompt(x) for x in pairs], ensure_ascii=False, indent=2)
    return f"""
Bạn là evaluator độc lập cho thí nghiệm CHVD.

Mỗi record có hai phiên bản của cùng một ý tưởng:
- source_candidate: bản concept gốc, có thể speculative.
- practical_revision: bản được viết lại theo hướng MVP thực tế hơn.

Bạn chỉ đánh giá nội dung trong card. Không dùng kiến thức web, benchmark, dữ liệu thị trường,
hay facts bên ngoài. Không suy đoán model, temperature hoặc pipeline phía sau hai phiên bản.

Chấm các metric từ 1.0 đến 5.0.

Metric càng CAO càng tốt:
- creative_core_preservation: practical_revision giữ lại cơ chế/spark sáng tạo trung tâm của source_candidate.
- novelty_retention: practical_revision vẫn giữ được độ khác biệt quan trọng của source_candidate.
- practical_mvp_improvement: practical_revision rõ hơn, khả thi hơn, và dễ prototype/test hơn source_candidate.
- overclaim_removal_quality: practical_revision loại hoặc hạ cấp các overclaim không được hỗ trợ bằng cách thay bằng giả định/phạm vi MVP hợp lý.

Metric càng THẤP càng tốt:
- semantic_drift_risk: practical_revision làm lệch hoặc thay thế concept trung tâm của source_candidate.

preferred_for_prototype:
- "A" nếu source_candidate phù hợp để prototype/test hơn.
- "B" nếu practical_revision phù hợp để prototype/test hơn.
- "TIE" nếu không có bên nào rõ ràng hơn.

Quy tắc:
- Không tự thêm sản phẩm, tính năng, dữ kiện, citation hoặc giải pháp mới.
- Không phạt source_candidate chỉ vì speculative; chỉ phản ánh sự thiếu khả thi/overclaim nếu card thể hiện điều đó.
- Rationale tối đa hai câu, nêu trade-off nội tại giữa source và revision.
- Phải trả đủ mọi pair_id trong batch.
- Chỉ output JSON hợp lệ, không markdown hay giải thích ngoài JSON.

JSON schema bắt buộc:
{{
  "evaluations": [
    {{
      "pair_id": "PAIRBLIND_0001",
      "scores": {{
        "creative_core_preservation": 1.0,
        "novelty_retention": 1.0,
        "practical_mvp_improvement": 1.0,
        "overclaim_removal_quality": 1.0,
        "semantic_drift_risk": 1.0
      }},
      "preferred_for_prototype": "B",
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
        "PerDay",
        "quotaValue",
    )
    return any(marker in message for marker in markers)


def evaluate_batch(
    *, api_key: str, model: str, pairs: List[Dict[str, Any]], max_output_tokens: int,
    retries: int, retry_sleep_base: float
) -> tuple[List[Dict[str, Any]], str]:
    if genai is None or types is None:
        raise RuntimeError("Thiếu package google-genai. Cài bằng: pip install -U google-genai")

    client = genai.Client(api_key=api_key)
    prompt = build_prompt(pairs)
    expected_pair_ids = [str(pair["pair_id"]) for pair in pairs]
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
            parsed = parse_json_object(raw_text)
            return normalize_evaluations(parsed, expected_pair_ids), raw_text
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            last_error = exc
            message = str(exc)
            if is_daily_quota_error(message):
                raise RuntimeError(
                    "Daily Gemini quota đã hết. Dừng ngay, không retry. "
                    "Đợi quota reset rồi chạy lại cùng lệnh; không dùng --force."
                ) from exc
            print(f"[WARN] Pairwise batch lỗi attempt {attempt}/{retries}: {exc}")
            if attempt < retries:
                sleep_s = min(60.0, retry_sleep_base * (2 ** (attempt - 1)))
                print(f"[WARN] Chờ {sleep_s:.1f}s rồi thử lại...")
                time.sleep(sleep_s)

    raise RuntimeError(f"Pairwise batch thất bại sau {retries} lần: {last_error}\nRaw excerpt: {raw_text[:800]}")


def existing_valid_score(path: Path, expected_pair_ids: Sequence[str]) -> bool:
    if not path.exists():
        return False
    try:
        payload = load_json(path)
        evaluations = normalize_evaluations(payload, expected_pair_ids)
        return len(evaluations) == len(expected_pair_ids)
    except Exception:
        return False


def write_jsonl(path: Path, records: Iterable[Dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run CHVD raw-to-distilled pairwise evaluation with Gemini.")
    parser.add_argument("--input", required=True, help="pairwise_distillation_input.jsonl from Step 1")
    parser.add_argument("--out-dir", required=True, help="Output folder for pairwise scores/checkpoints")
    parser.add_argument("--model", default="gemini-2.5-flash-lite")
    parser.add_argument("--api-key-env", default="GEMINI_JUDGE_KEY")
    parser.add_argument("--api-key", default="", help="Ưu tiên environment variable thay vì truyền key trực tiếp.")
    parser.add_argument("--batch-size", type=int, default=5)
    parser.add_argument("--max-output-tokens", type=int, default=6000)
    parser.add_argument("--retries", type=int, default=5)
    parser.add_argument("--retry-sleep-base", type=float, default=2.0)
    parser.add_argument("--force", action="store_true", help="Chấm lại cả batch đã có output hợp lệ.")
    parser.add_argument("--dry-run", action="store_true", help="Chỉ kiểm tra input/batches, không gọi API.")
    args = parser.parse_args()

    if args.batch_size < 1:
        raise ValueError("--batch-size phải >= 1")

    input_path = Path(args.input)
    out_dir = Path(args.out_dir)
    score_dir = out_dir / "pairwise_batch_scores"
    debug_dir = out_dir / "raw_debug"
    score_dir.mkdir(parents=True, exist_ok=True)
    debug_dir.mkdir(parents=True, exist_ok=True)

    pairs = load_jsonl(input_path)
    for pair in pairs:
        validate_pair_record(pair)
    pair_ids = [str(pair["pair_id"]) for pair in pairs]
    if len(pair_ids) != len(set(pair_ids)):
        raise ValueError("pair_id bị trùng trong pairwise input.")

    batches = list(chunked(pairs, args.batch_size))
    api_key = args.api_key.strip() or os.environ.get(args.api_key_env, "").strip()
    if not args.dry_run and not api_key:
        raise RuntimeError(f"Thiếu API key. PowerShell: $env:{args.api_key_env}=\"API_KEY\"")

    all_records: List[Dict[str, Any]] = []
    for index, batch in enumerate(batches, start=1):
        batch_name = f"pairwise_batch_{index:03d}"
        output_path = score_dir / f"{batch_name}.scores.json"
        expected_pair_ids = [str(pair["pair_id"]) for pair in batch]

        if args.dry_run:
            print(f"[DRY-RUN] {batch_name}: {len(batch)} pairs")
            continue

        if not args.force and existing_valid_score(output_path, expected_pair_ids):
            payload = load_json(output_path)
            records = normalize_evaluations(payload, expected_pair_ids)
            print(f"[SKIP] {batch_name}: output hợp lệ đã tồn tại")
        else:
            print(f"[INFO] Pairwise judge {index}/{len(batches)}: {batch_name} ({len(batch)} pairs)")
            records, raw_text = evaluate_batch(
                api_key=api_key,
                model=args.model,
                pairs=batch,
                max_output_tokens=args.max_output_tokens,
                retries=args.retries,
                retry_sleep_base=args.retry_sleep_base,
            )
            (debug_dir / f"{batch_name}.raw.txt").write_text(raw_text, encoding="utf-8")
            payload = {
                "metadata": {
                    "stage": "directional_pairwise_raw_to_chvd_judge",
                    "judge_model": args.model,
                    "temperature": 0.0,
                    "top_p": 0.1,
                    "input_source": str(input_path),
                    "batch_index": index,
                    "created_at": now_iso(),
                },
                "evaluations": records,
            }
            output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[OK] {output_path}")

        for record in records:
            all_records.append({
                **record,
                "judge_model": args.model,
                "batch_id": batch_name,
            })

    if not args.dry_run:
        all_records.sort(key=lambda x: x["pair_id"])
        combined = out_dir / "pairwise_distillation_judge_scores.jsonl"
        write_jsonl(combined, all_records)
        manifest = {
            "stage": "directional_pairwise_raw_to_chvd_judge",
            "judge_model": args.model,
            "temperature": 0.0,
            "top_p": 0.1,
            "metrics": PAIR_METRICS,
            "preference_values": sorted(PREFERENCE_VALUES),
            "num_pairs": len(pairs),
            "num_batches": len(batches),
            "created_at": now_iso(),
            "combined_output": str(combined),
            "notes": [
                "The judge sees Source candidate = raw M1 and Practical revision = CHVD M3.",
                "This is a directional source-to-revision assessment, not an order-randomized preference-only test.",
                "Never share pairwise_key_private.csv with the judge.",
            ],
        }
        (out_dir / "pairwise_judge_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"[OK] Combined pairwise scores: {combined.resolve()}")


if __name__ == "__main__":
    main()
