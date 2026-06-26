#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
02_filter_p1_shared_neutral_en.py

CHVD P1-M3 English - Filter tầng 2:
    ZERO-TEMPERATURE CREATIVE DISTILLER + REALISTIC MVP REFRAMER

Mục tiêu:
    Không chỉ chấm/rank idea.
    File này biến hallucination sáng tạo thành phiên bản thực tế hơn bằng 4 bước:

    1. Giữ lại creative core
    2. Tách/cắt factual overclaim hoặc hallucinated claim
    3. Reframe idea thành hướng MVP thực tế nhất
    4. Chấm điểm sau khi đã reframe

Triết lý:
    - Không xóa sạch hallucination.
    - Không tin toàn bộ hallucination.
    - Giữ "hạt nhân sáng tạo".
    - Loại bỏ claim giả/sai/không kiểm chứng.
    - Hiện thực hóa thành candidate MVP khả thi hơn.

Không dùng corpus.
Không dùng RAG.
Không có evidence_support trong điểm số.
Temperature mặc định = 0.0.

Cài thư viện:
    pip install -U google-genai

Set API key trên PowerShell:
    $env:GEMINI_FILTER_KEY="API_KEY_THU_HAI"

Ví dụ chạy:
    python 02_filter_p1_shared_neutral_en.py `
      --input outputs_v5/ideas_raw_vi.json `
      --model "gemini-2.5-flash-lite" `
      --temperature 0 `
      --top-p 0.1 `
      --max-output-tokens 8192

Output:
    outputs_v5_4/ideas_filtered_vi.json
    outputs_v5_4/ideas_ranked_vi.csv
    outputs_v5_4/filter_report_vi.md
    outputs_v5_4/filter_raw_response_debug.txt
    outputs_v5_4/filter_prompt_debug.txt
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from google import genai
from google.genai import types


# =============================================================================
# Utility
# =============================================================================

def ensure_dir(path: str | Path) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def now_iso() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def safe_json_loads(text: str) -> Optional[Any]:
    try:
        return json.loads(text)
    except Exception:
        return None


def extract_json(text: str, fallback: Any = None) -> Any:
    if not text:
        return fallback

    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)

    direct = safe_json_loads(text)
    if direct is not None:
        return direct

    starts = [i for i, ch in enumerate(text) if ch in "[{"]
    for start in starts:
        stack: List[str] = []
        in_string = False
        escape = False

        for i in range(start, len(text)):
            ch = text[i]

            if in_string:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
                continue

            if ch == '"':
                in_string = True
            elif ch in "[{":
                stack.append(ch)
            elif ch in "]}":
                if not stack:
                    break
                top = stack[-1]
                if (top == "[" and ch == "]") or (top == "{" and ch == "}"):
                    stack.pop()
                    if not stack:
                        candidate = text[start:i + 1]
                        parsed = safe_json_loads(candidate)
                        if parsed is not None:
                            return parsed
                        break
                else:
                    break

    return fallback


def call_gemini(
    api_key: str,
    model: str,
    prompt: str,
    temperature: float,
    top_p: float,
    max_output_tokens: int,
    retries: int,
    retry_sleep_base: float,
) -> str:
    client = genai.Client(api_key=api_key)
    last_error: Exception | None = None

    for attempt in range(1, retries + 1):
        try:
            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=temperature,
                    top_p=top_p,
                    max_output_tokens=max_output_tokens,
                    response_mime_type="application/json",
                ),
            )
            return response.text or ""
        except KeyboardInterrupt:
            raise
        except Exception as e:
            last_error = e
            print(f"[WARN] Lỗi Gemini attempt {attempt}/{retries}: {e}")
            if attempt < retries:
                sleep_s = min(30.0, retry_sleep_base * (2 ** (attempt - 1)))
                print(f"[WARN] Chờ {sleep_s:.1f}s rồi thử lại...")
                time.sleep(sleep_s)

    raise RuntimeError(f"Gọi Gemini thất bại sau {retries} lần thử: {last_error}")


# =============================================================================
# Prompt
# =============================================================================

def build_filter_prompt(ideas: List[Dict[str, Any]]) -> str:
    return f"""
You are STAGE 2 of the CHVD framework:
ZERO-TEMPERATURE CREATIVE DISTILLER + REALISTIC MVP REFRAMER.

You are not an independent idea generator.
Do not invent a separate startup concept.
Do not fabricate evidence, citations, market statistics, competitors, patents,
companies, or claims of external validation.
Work only from the supplied input ideas.

Your role is not to delete creativity.
Your role is to:
1. Separate the creative core from speculative or unsupported claims.
2. Preserve the distinctive creative mechanism that gives the idea its identity.
3. Remove, narrow, or soften unsupported, unsafe, or implausible claims.
4. Reframe the same idea as the most realistic testable MVP possible.
5. Score the reframed candidate, not the raw speculative version.

In other words:
The high-temperature generator supplies creative raw material.
You are a zero-temperature distiller that converts it into a testable MVP candidate.

Execution mode:
- prompt_only
- no_corpus
- no_RAG
- no_external_evidence
- internal_logic_only
- temperature_expected_zero

Input ideas:
{json.dumps(ideas, ensure_ascii=False, indent=2)}

Rules for every idea:

Step A — Decompose:
- Identify the original speculative or unsupported elements.
- Identify the creative core that must remain visible.
- Identify factual overclaims or mechanisms that should not be retained literally.

Step B — Realistic reframing:
- Do not replace the input with an unrelated startup idea.
- Preserve the original creative direction.
- Remove, narrow, or soften unsupported claims.
- Scope the MVP so that it can be prototyped in 7–30 days.
- When a speculative mechanism cannot be preserved literally, preserve a bounded
  proxy interaction that retains the original user experience or value mechanism.
- Do not replace a distinctive concept with a generic chatbot, marketplace,
  recommender, or content generator unless that generic mechanism is genuinely
  the original creative core.

Step C — Score the reframed candidate:
Score the reframed version only, not the raw speculative version.

Use a 1–5 scale. Decimal scores are allowed to one decimal place.

1. novelty:
   Low when the reframed candidate is generic or conventional; high when it
   retains a distinctive, non-obvious concept.

2. creative_core_strength:
   Low when there is no clear creative core; high when the input has a strong,
   distinctive mechanism worth preserving.

3. creative_core_preservation:
   Low when the reframing loses the original creative core; high when the
   practical MVP retains the important creative mechanism.

4. reframing_quality:
   Low when the output remains unrealistic or becomes generic; high when it
   becomes testable while preserving creative identity.

5. conceptual_coherence:
   Low when the problem, concept, approach, and MVP conflict or feel fragmented;
   high when they form a clear and consistent logic.

6. user_pain_plausibility:
   Low when the user problem or desire is unclear; high when it is concrete,
   understandable, and plausibly worth addressing.

7. technical_plausibility:
   Low when the MVP depends on distant or undefined technology; high when a
   clear implementation path is available within a bounded prototype.

8. mvp_clarity:
   Low when it is unclear what should be built; high when the MVP has a narrow,
   concrete, testable scope.

9. business_model_plausibility:
   Low when the value capture mechanism is unclear; high when there is a
   credible early monetization or value-exchange hypothesis.

10. hallucination_risk_after_reframing:
    Low when the reframed MVP has few unsupported, vague, or unverifiable
    claims; high when major speculative claims remain.
    This score must be an integer from 1 to 5.

There is no evidence_support metric.
Missing external evidence is not directly penalized because this is a
prompt-only creative ideation pipeline. However, unvalidated assumptions must
be listed under validation_needed_later.

Decision labels:
- "Potential": overall score >= 70 and hallucination_risk_after_reframing <= 3
- "Needs refinement": overall score 45–69 or the candidate still needs material narrowing
- "Too speculative": overall score < 45 or hallucination_risk_after_reframing >= 5

Deterministic overall_score formula, 0–100:

positive_score =
15% novelty
+ 12% creative_core_strength
+ 13% creative_core_preservation
+ 15% reframing_quality
+ 10% conceptual_coherence
+ 10% user_pain_plausibility
+ 10% technical_plausibility
+ 10% mvp_clarity
+ 5% business_model_plausibility

Then subtract the penalty for hallucination_risk_after_reframing:
risk 1: subtract 0
risk 2: subtract 5
risk 3: subtract 10
risk 4: subtract 18
risk 5: subtract 30

Important:
- Return 1–5 scores for every metric.
- overall_score and decision inside the LLM JSON are placeholders only.
- Python will recompute overall_score and decision deterministically.
- Do not inflate overall_score or decision.

Output requirements:
- All JSON values and all narrative text must be in English.
- Keep JSON keys in English.
- Return valid JSON only.
- Do not use Markdown.
- Do not add new independent ideas.
- Every evaluation must correspond to one input idea.

Required schema:

{{
  "metadata": {{
    "pipeline": "CHVD",
    "version": "P1_M3_en_v1",
    "stage": "zero_temperature_creative_distiller_to_realistic_mvp",
    "language": "en",
    "evaluation_mode": "prompt_only_internal_logic_creative_distillation",
    "description": "Separate the creative core from unsupported overclaims and reframe each input into a realistic MVP candidate."
  }},
  "evaluations": [
    {{
      "idea_id": "I001",
      "idea_name": "",
      "raw_hallucination_summary": "",
      "creative_core_to_preserve": "",
      "hallucinated_or_overclaim_parts_to_remove": [
        ""
      ],
      "realistic_reframing": "",
      "practical_mvp": "",
      "mvp_7_to_30_day_plan": [
        ""
      ],
      "scores": {{
        "novelty": 1.0,
        "creative_core_strength": 1.0,
        "creative_core_preservation": 1.0,
        "reframing_quality": 1.0,
        "conceptual_coherence": 1.0,
        "user_pain_plausibility": 1.0,
        "technical_plausibility": 1.0,
        "mvp_clarity": 1.0,
        "business_model_plausibility": 1.0,
        "hallucination_risk_after_reframing": 1
      }},
      "overall_score": 0,
      "decision": "Potential | Needs refinement | Too speculative",
      "expert_rationale": "",
      "strong_points_after_reframing": [
        ""
      ],
      "weak_points_after_reframing": [
        ""
      ],
      "validation_needed_later": [
        ""
      ],
      "final_recommendation": ""
    }}
  ],
  "ranked_summary": [
    {{
      "rank": 1,
      "idea_id": "I001",
      "idea_name": "",
      "overall_score": 0,
      "decision": "Potential | Needs refinement | Too speculative",
      "hallucination_risk_after_reframing": 1
    }}
  ]
}}
""".strip()


# =============================================================================
# Normalize + deterministic scoring
# =============================================================================

SCORE_KEYS = [
    "novelty",
    "creative_core_strength",
    "creative_core_preservation",
    "reframing_quality",
    "conceptual_coherence",
    "user_pain_plausibility",
    "technical_plausibility",
    "mvp_clarity",
    "business_model_plausibility",
    "hallucination_risk_after_reframing",
]

# Tổng weight = 1.0. Đây là công thức deterministic dùng cho paper.
# LLM chỉ chấm từng metric 1-5; code tự tính overall_score.
WEIGHTS = {
    "novelty": 0.15,
    "creative_core_strength": 0.12,
    "creative_core_preservation": 0.13,
    "reframing_quality": 0.15,
    "conceptual_coherence": 0.10,
    "user_pain_plausibility": 0.10,
    "technical_plausibility": 0.10,
    "mvp_clarity": 0.10,
    "business_model_plausibility": 0.05,
}

RISK_PENALTY = {1: 0, 2: 5, 3: 10, 4: 18, 5: 30}


def clamp_score(value: Any, default: float = 3.0) -> float:
    try:
        x = float(value)
    except Exception:
        x = default
    return max(1.0, min(5.0, x))


def compute_overall(scores: Dict[str, float]) -> float:
    positive_1_to_5 = sum(scores[k] * w for k, w in WEIGHTS.items())
    positive_0_to_100 = (positive_1_to_5 - 1.0) / 4.0 * 100.0

    risk = int(round(clamp_score(scores.get("hallucination_risk_after_reframing", 3), default=3)))
    penalty = RISK_PENALTY.get(risk, 10)

    return round(max(0.0, min(100.0, positive_0_to_100 - penalty)), 2)


def decide(overall: float, risk: float, mvp: float, reframe: float) -> str:
    if overall >= 70 and risk <= 3 and mvp >= 3 and reframe >= 3:
        return "Potential"
    if overall < 45 or risk >= 5 or mvp <= 1 or reframe <= 1:
        return "Too speculative"
    return "Needs refinement"


def normalize_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(x) for x in value if str(x).strip()]
    if isinstance(value, str):
        return [value] if value.strip() else []
    return [str(value)]


def idea_lookup(ideas: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {str(i.get("idea_id", "")): i for i in ideas}


def normalize_filter_output(obj: Any, ideas: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(obj, dict):
        raise ValueError("Filter output không phải JSON object.")

    evaluations = obj.get("evaluations")
    if not isinstance(evaluations, list):
        raise ValueError(f"Filter output thiếu evaluations. Các key nhận được: {list(obj.keys())}")

    lookup = idea_lookup(ideas)
    idea_name_map = {k: v.get("idea_name", "") for k, v in lookup.items()}

    clean: List[Dict[str, Any]] = []
    seen: set[str] = set()

    for idx, ev in enumerate(evaluations, start=1):
        if not isinstance(ev, dict):
            continue

        idea_id = str(ev.get("idea_id") or f"I{idx:03d}")
        seen.add(idea_id)
        idea_name = str(ev.get("idea_name") or idea_name_map.get(idea_id, f"Ý tưởng {idx}"))
        raw_scores = ev.get("scores") if isinstance(ev.get("scores"), dict) else {}
        scores = {k: clamp_score(raw_scores.get(k), default=3.0) for k in SCORE_KEYS}

        # Deterministic scoring: tuyệt đối không dùng overall_score/decision do LLM tự ghi.
        llm_reported_overall_score = ev.get("overall_score")
        llm_reported_decision = ev.get("decision")

        overall = compute_overall(scores)
        decision = decide(
            overall=overall,
            risk=scores["hallucination_risk_after_reframing"],
            mvp=scores["mvp_clarity"],
            reframe=scores["reframing_quality"],
        )

        clean.append({
            "idea_id": idea_id,
            "idea_name": idea_name,
            "raw_hallucination_summary": str(ev.get("raw_hallucination_summary") or ""),
            "creative_core_to_preserve": str(ev.get("creative_core_to_preserve") or ""),
            "hallucinated_or_overclaim_parts_to_remove": normalize_list(ev.get("hallucinated_or_overclaim_parts_to_remove")),
            "realistic_reframing": str(ev.get("realistic_reframing") or ""),
            "practical_mvp": str(ev.get("practical_mvp") or ""),
            "mvp_7_to_30_day_plan": normalize_list(ev.get("mvp_7_to_30_day_plan")),
            "scores": scores,
            "overall_score": overall,
            "decision": decision,
            "llm_reported_overall_score": llm_reported_overall_score,
            "llm_reported_decision": llm_reported_decision,
            "expert_rationale": str(ev.get("expert_rationale") or ""),
            "strong_points_after_reframing": normalize_list(ev.get("strong_points_after_reframing")),
            "weak_points_after_reframing": normalize_list(ev.get("weak_points_after_reframing")),
            "validation_needed_later": normalize_list(ev.get("validation_needed_later")),
            "final_recommendation": str(ev.get("final_recommendation") or ""),
        })

    # Fallback nếu model bỏ sót idea.
    for idea in ideas:
        idea_id = str(idea.get("idea_id", ""))
        if idea_id and idea_id not in seen:
            scores = {
                "novelty": 3.0,
                "creative_core_strength": 3.0,
                "creative_core_preservation": 2.0,
                "reframing_quality": 2.0,
                "conceptual_coherence": 2.0,
                "user_pain_plausibility": 2.0,
                "technical_plausibility": 2.0,
                "mvp_clarity": 2.0,
                "business_model_plausibility": 2.0,
                "hallucination_risk_after_reframing": 4.0,
            }
            overall = compute_overall(scores)
            clean.append({
                "idea_id": idea_id,
                "idea_name": idea.get("idea_name", ""),
                "raw_hallucination_summary": "The filter omitted this idea.",
                "creative_core_to_preserve": "",
                "hallucinated_or_overclaim_parts_to_remove": ["Re-run the filter to obtain a complete analysis."],
                "realistic_reframing": "",
                "practical_mvp": "",
                "mvp_7_to_30_day_plan": [],
                "scores": scores,
                "overall_score": overall,
                "decision": "Needs refinement",
                "expert_rationale": "Fallback evaluation because the filter omitted this idea.",
                "strong_points_after_reframing": [],
                "weak_points_after_reframing": ["The idea was not analyzed completely."],
                "validation_needed_later": ["Re-run the filter or inspect the output schema."],
                "final_recommendation": "Do not use this fallback result as evidence in the paper.",
            })

    clean.sort(key=lambda x: x["overall_score"], reverse=True)

    ranked = []
    for rank, ev in enumerate(clean, start=1):
        ranked.append({
            "rank": rank,
            "idea_id": ev["idea_id"],
            "idea_name": ev["idea_name"],
            "overall_score": ev["overall_score"],
            "decision": ev["decision"],
            "hallucination_risk_after_reframing": ev["scores"]["hallucination_risk_after_reframing"],
        })

    obj.setdefault("metadata", {})
    obj["metadata"].update({
        "pipeline": "CHVD",
        "version": "P1_M3_en_v1",
        "stage": "zero_temperature_creative_distiller_to_realistic_mvp",
        "language": "en",
        "evaluation_mode": "prompt_only_internal_logic_creative_distillation",
        "normalized_at": now_iso(),
    })
    obj["evaluations"] = clean
    obj["ranked_summary"] = ranked
    return obj


# =============================================================================
# Export
# =============================================================================

def write_csv(path: Path, evaluations: List[Dict[str, Any]], ideas: List[Dict[str, Any]]) -> None:
    lookup = idea_lookup(ideas)
    rows: List[Dict[str, Any]] = []

    for ev in evaluations:
        idea = lookup.get(ev["idea_id"], {})
        row = {
            "idea_id": ev["idea_id"],
            "idea_name": ev["idea_name"],
            "decision": ev["decision"],
            "overall_score": ev["overall_score"],
            "llm_reported_overall_score": ev.get("llm_reported_overall_score", ""),
            "llm_reported_decision": ev.get("llm_reported_decision", ""),
            "hallucination_risk_after_reframing": ev["scores"]["hallucination_risk_after_reframing"],
            "original_pitch": idea.get("one_sentence_pitch", ""),
            "target_user": idea.get("target_user", ""),
            "raw_hallucination_summary": ev["raw_hallucination_summary"],
            "creative_core_to_preserve": ev["creative_core_to_preserve"],
            "realistic_reframing": ev["realistic_reframing"],
            "practical_mvp": ev["practical_mvp"],
            "expert_rationale": ev["expert_rationale"],
            "final_recommendation": ev["final_recommendation"],
            "hallucinated_or_overclaim_parts_to_remove": " | ".join(ev["hallucinated_or_overclaim_parts_to_remove"]),
            "mvp_7_to_30_day_plan": " | ".join(ev["mvp_7_to_30_day_plan"]),
            "strong_points_after_reframing": " | ".join(ev["strong_points_after_reframing"]),
            "weak_points_after_reframing": " | ".join(ev["weak_points_after_reframing"]),
            "validation_needed_later": " | ".join(ev["validation_needed_later"]),
        }
        for k in SCORE_KEYS:
            row[k] = ev["scores"].get(k, "")
        rows.append(row)

    if not rows:
        path.write_text("", encoding="utf-8")
        return

    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def write_markdown_report(path: Path, evaluations: List[Dict[str, Any]], ideas: List[Dict[str, Any]], metadata: Dict[str, Any]) -> None:
    lookup = idea_lookup(ideas)
    lines: List[str] = []

    lines.append("# CHVD P1-M3 Report — Creative Core to Realistic MVP")
    lines.append("")
    lines.append(f"- Generated at: {now_iso()}")
    lines.append(f"- Filter model: `{metadata.get('filter_model', '')}`")
    lines.append(f"- Filter temperature: `{metadata.get('filter_temperature', '')}`")
    lines.append(f"- Evaluation mode: `{metadata.get('evaluation_mode', '')}`")
    lines.append(f"- Scoring mode: `{metadata.get('scoring_mode', 'deterministic_python' )}`")
    lines.append("")
    lines.append("> This stage preserves the creative core, narrows unsupported claims, and reframes each concept as a realistic MVP. `overall_score` and `decision` are computed deterministically in Python rather than taken from the LLM.")
    lines.append("")

    lines.append("## Ranking")
    lines.append("")
    lines.append("| Rank | Idea | Score | Decision | Risk After Reframing | Creative Core | Core Preservation | Reframing | MVP Clarity |")
    lines.append("|---:|---|---:|---|---:|---:|---:|---:|---:|")
    for rank, ev in enumerate(evaluations, start=1):
        s = ev["scores"]
        lines.append(
            f"| {rank} | {ev['idea_name']} | {ev['overall_score']} | {ev['decision']} | "
            f"{s['hallucination_risk_after_reframing']} | {s['creative_core_strength']} | "
            f"{s.get('creative_core_preservation', '')} | {s['reframing_quality']} | {s['mvp_clarity']} |"
        )

    lines.append("")
    lines.append("## Detailed evaluations")
    lines.append("")

    for rank, ev in enumerate(evaluations, start=1):
        idea = lookup.get(ev["idea_id"], {})
        s = ev["scores"]

        lines.append(f"### {rank}. {ev['idea_name']} — {ev['decision']} — {ev['overall_score']}/100")
        lines.append("")
        lines.append(f"- **Original pitch:** {idea.get('one_sentence_pitch', '')}")
        lines.append(f"- **Raw speculation summary:** {ev['raw_hallucination_summary']}")
        lines.append(f"- **Creative core to preserve:** {ev['creative_core_to_preserve']}")
        lines.append(f"- **Realistic reframing:** {ev['realistic_reframing']}")
        lines.append(f"- **Practical MVP:** {ev['practical_mvp']}")
        lines.append(f"- **Expert rationale:** {ev['expert_rationale']}")
        lines.append(f"- **Final recommendation:** {ev['final_recommendation']}")
        lines.append("")

        def add_section(title: str, values: List[str]) -> None:
            if values:
                lines.append(f"**{title}:**")
                for value in values:
                    lines.append(f"- {value}")
                lines.append("")

        add_section("Unsupported or overclaim elements to remove", ev["hallucinated_or_overclaim_parts_to_remove"])
        add_section("7–30 day MVP plan", ev["mvp_7_to_30_day_plan"])
        add_section("Strengths after reframing", ev["strong_points_after_reframing"])
        add_section("Weaknesses after reframing", ev["weak_points_after_reframing"])
        add_section("Validation needed later", ev["validation_needed_later"])

        lines.append("**Scores:**")
        for key in SCORE_KEYS:
            lines.append(f"- `{key}`: {s.get(key, '')}")
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


# =============================================================================
# Main
# =============================================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description="CHVD P1-M3 English Filter: distill hallucination into realistic MVP, zero temperature."
    )
    parser.add_argument("--input", type=str, default="outputs_v5_3/ideas_raw_vi.json")
    parser.add_argument("--model", type=str, default="gemini-2.5-flash-lite")
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--top-p", type=float, default=0.1)
    parser.add_argument("--max-output-tokens", type=int, default=8192)
    parser.add_argument("--retries", type=int, default=5)
    parser.add_argument("--retry-sleep-base", type=float, default=2.0)
    parser.add_argument("--api-key-env", type=str, default="GEMINI_FILTER_KEY")
    parser.add_argument("--api-key", type=str, default="")
    parser.add_argument("--out-dir", type=str, default="outputs_v5_4")
    parser.add_argument("--out-json", type=str, default="ideas_filtered_vi.json")
    parser.add_argument("--out-csv", type=str, default="ideas_ranked_vi.csv")
    parser.add_argument("--out-md", type=str, default="filter_report_vi.md")
    args = parser.parse_args()

    api_key = args.api_key.strip() or os.environ.get(args.api_key_env, "").strip()
    if not api_key:
        raise RuntimeError(
            f"Thiếu API key. Hãy set PowerShel[LOCAL_PATH_REDACTED]"
            f'$env:{args.api_key_env}="API_KEY_THU_HAI"'
        )

    input_path = Path(args.input)
    if not input_path.exists():
        raise FileNotFoundError(f"Không tìm thấy input file: {input_path}")

    loaded = json.loads(input_path.read_text(encoding="utf-8"))
    ideas = loaded.get("ideas")
    if not isinstance(ideas, list) or not ideas:
        raise RuntimeError("Input JSON không có key 'ideas' hợp lệ.")

    out_dir = ensure_dir(args.out_dir)
    out_json_path = out_dir / args.out_json
    out_csv_path = out_dir / args.out_csv
    out_md_path = out_dir / args.out_md
    raw_debug_path = out_dir / "filter_raw_response_debug.txt"
    prompt_debug_path = out_dir / "filter_prompt_debug.txt"

    prompt = build_filter_prompt(ideas)
    prompt_debug_path.write_text(prompt, encoding="utf-8")

    print("[INFO] CHVD P1-M3 English Filter")
    print("[INFO] Mode: creative distillation -> realistic MVP")
    print("[INFO] Recommended temperature: 0.0")
    print(f"[INFO] Model: {args.model}")
    print(f"[INFO] Temperature: {args.temperature}")
    print(f"[INFO] Top-p: {args.top_p}")
    print(f"[INFO] Number of ideas: {len(ideas)}")

    raw_text = call_gemini(
        api_key=api_key,
        model=args.model,
        prompt=prompt,
        temperature=args.temperature,
        top_p=args.top_p,
        max_output_tokens=args.max_output_tokens,
        retries=args.retries,
        retry_sleep_base=args.retry_sleep_base,
    )

    raw_debug_path.write_text(raw_text, encoding="utf-8")

    parsed = extract_json(raw_text, fallback=None)
    try:
        parsed = normalize_filter_output(parsed, ideas)
    except Exception as e:
        print("[ERROR] Could not parse or normalize the filter output.")
        print(f"[ERROR] {e}")
        print(f"[DEBUG] Raw response: {raw_debug_path.resolve()}")
        print("[DEBUG] First 1200 characters:")
        print((raw_text or "")[:1200])
        raise

    parsed["metadata"]["filter_model"] = args.model
    parsed["metadata"]["filter_temperature"] = args.temperature
    parsed["metadata"]["filter_top_p"] = args.top_p
    parsed["metadata"]["input_file"] = str(input_path)
    parsed["metadata"]["source_generator_metadata"] = loaded.get("metadata", {})
    parsed["metadata"]["raw_filter_text"] = raw_text
    parsed["metadata"]["output_language_requirement"] = "English only"

    out_json_path.write_text(json.dumps(parsed, ensure_ascii=False, indent=2), encoding="utf-8")
    write_csv(out_csv_path, parsed["evaluations"], ideas)
    write_markdown_report(out_md_path, parsed["evaluations"], ideas, parsed["metadata"])

    print(f"[OK] Distilled and evaluated {len(parsed['evaluations'])} ideas.")
    print(f"[OK] JSON: {out_json_path.resolve()}")
    print(f"[OK] CSV:  {out_csv_path.resolve()}")
    print(f"[OK] Markdown report: {out_md_path.resolve()}")
    print(f"[OK] Raw debug: {raw_debug_path.resolve()}")


if __name__ == "__main__":
    main()
