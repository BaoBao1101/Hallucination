#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CHVD Blind Evaluation Toolkit — Step 2

Run a blind, independent metric judge over public candidate batches produced by
01_shuffle_merge_blind_dataset.py.

This script never receives private method labels. It only reads public cards.
It saves every batch, raw response, normalized scores, and can resume safely.

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
except ImportError:  # Allows --dry-run and offline checks before installing the SDK.
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


def now_iso() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_batches(batch_dir: Path) -> List[Path]:
    paths = sorted(batch_dir.glob("batch_*.json"))
    if not paths:
        raise FileNotFoundError(f"Không tìm thấy batch_*.json trong: {batch_dir}")
    return paths


def strip_json_fence(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def parse_json_object(text: str) -> Dict[str, Any]:
    cleaned = strip_json_fence(text)
    try:
        value = json.loads(cleaned)
    except Exception as exc:
        raise ValueError(f"Judge trả JSON lỗi: {exc}") from exc
    if isinstance(value, list):
        value = {"evaluations": value}
    if not isinstance(value, dict):
        raise ValueError("Judge output phải là JSON object hoặc list.")
    return value


def clamp_score(value: Any, metric: str) -> float:
    try:
        score = float(value)
    except Exception as exc:
        raise ValueError(f"Metric '{metric}' không phải số: {value!r}") from exc
    if score < 1.0 or score > 5.0:
        raise ValueError(f"Metric '{metric}' phải nằm trong [1, 5], nhận {score}.")
    return round(score, 2)


def normalize_evaluations(payload: Dict[str, Any], expected_ids: Sequence[str]) -> List[Dict[str, Any]]:
    evaluations = payload.get("evaluations", payload.get("results"))
    if not isinstance(evaluations, list):
        raise ValueError("Judge output thiếu list 'evaluations'.")

    expected = set(expected_ids)
    received: set[str] = set()
    normalized: List[Dict[str, Any]] = []

    for item in evaluations:
        if not isinstance(item, dict):
            raise ValueError("Mỗi evaluation phải là JSON object.")
        candidate_id = str(item.get("candidate_id", "")).strip()
        if candidate_id not in expected:
            raise ValueError(f"candidate_id không thuộc batch: {candidate_id!r}")
        if candidate_id in received:
            raise ValueError(f"candidate_id bị trùng trong judge output: {candidate_id}")
        received.add(candidate_id)

        scores = item.get("scores")
        if not isinstance(scores, dict):
            raise ValueError(f"Thiếu scores cho {candidate_id}")
        clean_scores = {metric: clamp_score(scores.get(metric), metric) for metric in METRICS}
        rationale = str(item.get("short_rationale", "")).strip()
        normalized.append({
            "candidate_id": candidate_id,
            "scores": clean_scores,
            "short_rationale": rationale[:700],
        })

    missing = expected - received
    if missing:
        raise ValueError(f"Judge thiếu {len(missing)} candidate_id: {sorted(missing)}")
    if len(normalized) != len(expected_ids):
        raise ValueError("Số evaluation không khớp số candidate trong batch.")
    return normalized


def build_prompt(cards: List[Dict[str, Any]]) -> str:
    cards_json = json.dumps(cards, ensure_ascii=False, indent=2)
    return f"""
Bạn là independent blind evaluator trong thí nghiệm CHVD.

Bạn chỉ được đánh giá nội dung của từng candidate card. Không suy đoán candidate thuộc phương pháp,
temperature, pipeline, hay model nào. Không dùng kiến thức web, số liệu thị trường, citation hoặc facts bên ngoài.
Chỉ dùng internal logic từ nội dung card.

Thang điểm cho tất cả metric: 1.0 đến 5.0 theo FLOAT (chấm chính xác đến từng số thập phân, không làm tròn điểm)

Metric càng CAO càng tốt:
- novelty: mức khác biệt/không rập khuôn của ý tưởng (Tính mới của ý tưởng)
- conceptual_coherence: logic nội tại giữa nhu cầu, concept, giải pháp và prototype.
- user_pain_plausibility: nhu cầu/desire có hợp lý ở mức giả thuyết không.
- technical_plausibility: prototype có thể xây bằng công nghệ/phạm vi mô tả không.
- mvp_clarity: phạm vi MVP có cụ thể, có thể thử nghiệm không.
- business_model_plausibility: giả thuyết giá trị/kiếm tiền có hợp lý không.
- overall_mvp_usefulness: candidate có hữu ích như một MVP candidate để thử nghiệm không.
- prototype_willingness: mức bạn sẵn sàng ưu tiên prototype/test candidate này.

Metric càng THẤP càng tốt:
- unsupported_claim_risk: mức idea dựa vào claim kỹ thuật/thị trường/tác động chưa được hỗ trợ ngay trong card.
- ethical_privacy_risk: mức rủi ro đạo đức, quyền riêng tư, thao túng, hoặc sử dụng dữ liệu nhạy cảm.

Quy tắc:
- Không phạt ý tưởng chỉ vì nó sáng tạo hoặc speculative; chỉ phạt khi nó khẳng định quá mức mà không có đường MVP rõ.
- Không thêm fact mới, không tự sửa idea, không đánh giá dựa trên việc bạn biết có sản phẩm tương tự hay không.
- Rationale tối đa 2 câu, nêu lý do nội tại ngắn gọn.
- Trả đúng toàn bộ candidate_id trong batch.
- Chỉ output JSON hợp lệ, không markdown, không giải thích ngoài JSON.

JSON schema bắt buộc:
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


def evaluate_batch(
    *, api_key: str, model: str, cards: List[Dict[str, Any]], max_output_tokens: int,
    retries: int, retry_sleep_base: float
) -> tuple[List[Dict[str, Any]], str]:
    if genai is None or types is None:
        raise RuntimeError("Thiếu package google-genai. Cài bằng: pip install -U google-genai")
    client = genai.Client(api_key=api_key)
    prompt = build_prompt(cards)
    expected_ids = [str(card["candidate_id"]) for card in cards]
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
            parsed = parse_json_object(raw_text)
            return normalize_evaluations(parsed, expected_ids), raw_text
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            last_error = exc
            print(f"[WARN] Judge batch lỗi attempt {attempt}/{retries}: {exc}")
            if attempt < retries:
                sleep_s = min(30.0, retry_sleep_base * (2 ** (attempt - 1)))
                print(f"[WARN] Chờ {sleep_s:.1f}s rồi thử lại...")
                time.sleep(sleep_s)

    raise RuntimeError(f"Judge batch thất bại sau {retries} lần: {last_error}\nRaw excerpt: {raw_text[:800]}")


def existing_valid_score(path: Path, expected_ids: Sequence[str]) -> bool:
    if not path.exists():
        return False
    try:
        payload = load_json(path)
        normalized = normalize_evaluations(payload, expected_ids)
        return len(normalized) == len(expected_ids)
    except Exception:
        return False


def write_jsonl(path: Path, records: Iterable[Dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run blind individual CHVD metric evaluation with Gemini.")
    parser.add_argument("--batch-dir", required=True, help="Folder produced by Step 1: .../batches")
    parser.add_argument("--out-dir", required=True, help="Output folder for judge scores/checkpoints")
    parser.add_argument("--model", default="gemini-2.5-flash-lite")
    parser.add_argument("--api-key-env", default="GEMINI_JUDGE_KEY")
    parser.add_argument("--api-key", default="", help="Ưu tiên dùng environment variable thay vì truyền key trực tiếp.")
    parser.add_argument("--max-output-tokens", type=int, default=6000)
    parser.add_argument("--retries", type=int, default=5)
    parser.add_argument("--retry-sleep-base", type=float, default=2.0)
    parser.add_argument("--force", action="store_true", help="Chấm lại cả batch đã có output hợp lệ.")
    parser.add_argument("--dry-run", action="store_true", help="Chỉ kiểm tra batch, không gọi API.")
    args = parser.parse_args()

    batch_dir = Path(args.batch_dir)
    out_dir = Path(args.out_dir)
    score_dir = out_dir / "batch_scores"
    debug_dir = out_dir / "raw_debug"
    score_dir.mkdir(parents=True, exist_ok=True)
    debug_dir.mkdir(parents=True, exist_ok=True)

    batch_paths = load_batches(batch_dir)
    api_key = args.api_key.strip() or os.environ.get(args.api_key_env, "").strip()
    if not args.dry_run and not api_key:
        raise RuntimeError(f"Thiếu API key. PowerShell: $env:{args.api_key_env}=\"API_KEY\"")

    all_records: List[Dict[str, Any]] = []
    for index, batch_path in enumerate(batch_paths, start=1):
        cards = load_json(batch_path)
        if not isinstance(cards, list) or not cards:
            raise ValueError(f"Batch không phải list không-rỗng: {batch_path}")
        expected_ids = [str(card.get("candidate_id", "")) for card in cards]
        if any(not x for x in expected_ids):
            raise ValueError(f"Thiếu candidate_id trong {batch_path}")

        output_path = score_dir / f"{batch_path.stem}.scores.json"
        if args.dry_run:
            print(f"[DRY-RUN] {batch_path.name}: {len(cards)} candidates")
            continue

        if not args.force and existing_valid_score(output_path, expected_ids):
            payload = load_json(output_path)
            records = normalize_evaluations(payload, expected_ids)
            print(f"[SKIP] {batch_path.name}: output hợp lệ đã tồn tại")
        else:
            print(f"[INFO] Judge {index}/{len(batch_paths)}: {batch_path.name} ({len(cards)} candidates)")
            records, raw_text = evaluate_batch(
                api_key=api_key,
                model=args.model,
                cards=cards,
                max_output_tokens=args.max_output_tokens,
                retries=args.retries,
                retry_sleep_base=args.retry_sleep_base,
            )
            (debug_dir / f"{batch_path.stem}.raw.txt").write_text(raw_text, encoding="utf-8")
            payload = {
                "metadata": {
                    "stage": "blind_individual_metric_judge",
                    "judge_model": args.model,
                    "temperature": 0.0,
                    "top_p": 0.1,
                    "batch_source": str(batch_path),
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
                "batch_id": batch_path.stem,
            })

    if not args.dry_run:
        all_records.sort(key=lambda x: x["candidate_id"])
        combined = out_dir / "blind_individual_judge_scores.jsonl"
        write_jsonl(combined, all_records)
        manifest = {
            "stage": "blind_individual_metric_judge",
            "judge_model": args.model,
            "temperature": 0.0,
            "top_p": 0.1,
            "metrics": METRICS,
            "num_batches": len(batch_paths),
            "num_evaluated_candidates": len(all_records),
            "created_at": now_iso(),
            "combined_output": str(combined),
        }
        (out_dir / "judge_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[OK] Combined scores: {combined.resolve()}")


if __name__ == "__main__":
    main()
