#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
01_generator_p1_shared_neutral.py

CHVD P1 — Shared Neutral Venture Ideation Generator
====================================================

Purpose
-------
This generator is designed for a CLEAN M0/M1 comparison:

    M0 = same prompt + low temperature / low top-p
    M1 = same prompt + high temperature / high top-p
    M3 = a separate distillation step that receives M1 output

The prompt intentionally avoids terms such as:
- hallucination
- wild / bizarre / extremely strange
- maximal divergent creativity
- blue ocean
- "must be crazy"

It still requires distinct, original, testable venture concepts.

Compatibility
-------------
The output keeps the current CHVD legacy fields required by the existing
M3 filter, while preserving the compact P1 fields:
    title, problem_or_desire, core_concept,
    proposed_approach, prototype_scope

Install
-------
    pip install -U google-genai

PowerShell API key
------------------
    $env:GEMINI_GENERATOR_KEY="YOUR_API_KEY"

Recommended M0 run
------------------
    python .\01_generator_p1_shared_neutral.py `
      --condition M0 `
      --domain "Edtech" `
      --num-ideas 5 `
      --temperature 0.0 `
      --top-p 0.1 `
      --out-dir .\outputs_P1\M0_edtech `
      --out-file ideas_raw_direct_edtech.json

Recommended M1 run
------------------
    python .\01_generator_p1_shared_neutral.py `
      --condition M1 `
      --domain "Edtech" `
      --num-ideas 5 `
      --temperature 2.0 `
      --top-p 0.99 `
      --out-dir .\outputs_P1\M1_edtech `
      --out-file ideas_raw_edtech.json

Then feed M1 output into the existing M3 filter:
    python .\02_filter_vi_low_temp.py `
      --input .\outputs_P1\M1_edtech\ideas_raw_edtech.json `
      --temperature 0.0 `
      --top-p 0.1 `
      --out-dir .\outputs_P1\M3_edtech `
      --out-json ideas_filtered_edtech.json
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from google import genai
from google.genai import types


P1_PROMPT_REGIME = "P1_shared_neutral_creative_ideation"
P1_VERSION = "P1_v1"


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def ensure_dir(path: str | Path) -> Path:
    output_dir = Path(path)
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def now_iso() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def strip_json_fence(text: str) -> str:
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def safe_json_loads(text: str) -> Optional[Any]:
    if not isinstance(text, str) or not text.strip():
        return None
    try:
        return json.loads(text)
    except Exception:
        try:
            return json.JSONDecoder(strict=False).decode(text)
        except Exception:
            return None


def iter_balanced_json_candidates(text: str) -> List[str]:
    """Extract balanced JSON object/array substrings, largest first."""
    candidates: List[str] = []
    starts = [i for i, char in enumerate(text) if char in "[{"]

    for start in starts:
        stack: List[str] = []
        in_string = False
        escaping = False

        for index in range(start, len(text)):
            char = text[index]

            if in_string:
                if escaping:
                    escaping = False
                elif char == "\\":
                    escaping = True
                elif char == '"':
                    in_string = False
                continue

            if char == '"':
                in_string = True
            elif char in "[{":
                stack.append(char)
            elif char in "]}":
                if not stack:
                    break
                opening = stack[-1]
                if (opening == "[" and char == "]") or (
                    opening == "{" and char == "}"
                ):
                    stack.pop()
                    if not stack:
                        candidates.append(text[start:index + 1])
                        break
                else:
                    break

    return sorted(candidates, key=len, reverse=True)


def looks_like_idea(item: Any) -> bool:
    if not isinstance(item, dict):
        return False
    expected = {
        "idea_id",
        "title",
        "idea_name",
        "problem_or_desire",
        "hidden_pain_or_desire",
        "core_concept",
        "proposed_approach",
        "prototype_scope",
    }
    return len(set(item.keys()) & expected) >= 2


def extract_json(text: str) -> Optional[Dict[str, Any]]:
    """
    Accept:
      {"metadata": ..., "ideas": [...]}
      [...]
    and recover a balanced JSON object if surrounding text exists.
    """
    clean_text = strip_json_fence(text)
    direct = safe_json_loads(clean_text)

    def wrap(value: Any) -> Optional[Dict[str, Any]]:
        if isinstance(value, dict) and isinstance(value.get("ideas"), list):
            return value
        if isinstance(value, list) and value and all(
            isinstance(item, dict) for item in value
        ):
            return {
                "metadata": {"schema_recovered_from": "top_level_idea_list"},
                "ideas": value,
            }
        return None

    wrapped = wrap(direct)
    if wrapped is not None:
        return wrapped

    for candidate in iter_balanced_json_candidates(clean_text):
        wrapped = wrap(safe_json_loads(candidate))
        if wrapped is not None:
            return wrapped

    return None


def call_gemini(
    *,
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
    last_error: Optional[Exception] = None

    daily_quota_markers = (
        "GenerateRequestsPerDayPerProjectPerModel-FreeTier",
        "RequestsPerDay",
        "quotaValue",
    )

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
        except Exception as exc:
            last_error = exc
            message = str(exc)

            # Retrying a daily quota error wastes quota/time and will not help.
            if any(marker in message for marker in daily_quota_markers):
                raise RuntimeError(
                    "Daily Gemini quota appears exhausted. Stop retrying and "
                    "run again after the quota resets."
                ) from exc

            print(f"[WARN] Gemini error attempt {attempt}/{retries}: {exc}")
            if attempt < retries:
                sleep_seconds = min(
                    30.0,
                    retry_sleep_base * (2 ** (attempt - 1)),
                )
                print(f"[WARN] Waiting {sleep_seconds:.1f}s before retry...")
                time.sleep(sleep_seconds)

    raise RuntimeError(
        f"Gemini generation failed after {retries} attempts: {last_error}"
    )


def repair_json_with_gemini(
    *,
    api_key: str,
    model: str,
    raw_text: str,
    max_output_tokens: int,
    retries: int,
    retry_sleep_base: float,
) -> str:
    """
    Syntax-only repair. It must not invent ideas or change semantic content.
    """
    repair_prompt = f"""
You are a JSON syntax repairer.

Convert the raw text below into valid JSON only.

Mandatory rules:
- Do not create new venture ideas.
- Do not remove an idea unless the text is irrecoverably incomplete.
- Do not add factual claims or details.
- Only repair JSON syntax: quotes, escaping, commas, brackets, and braces.
- The top-level JSON must contain "metadata" and "ideas".
- "ideas" must be a list of objects.
- Output JSON only. No Markdown.

RAW TEXT:
{raw_text}
""".strip()

    return call_gemini(
        api_key=api_key,
        model=model,
        prompt=repair_prompt,
        temperature=0.0,
        top_p=0.1,
        max_output_tokens=max_output_tokens,
        retries=retries,
        retry_sleep_base=retry_sleep_base,
    )


# ---------------------------------------------------------------------------
# Shared neutral prompt P1
# ---------------------------------------------------------------------------

def build_p1_prompt(
    *,
    domain: str,
    num_ideas: int,
    user_direction: str = "",
) -> str:
    direction_block = (
        user_direction.strip()
        if user_direction.strip()
        else "No additional direction. Follow the shared requirements exactly."
    )

    return f"""
You are a startup venture ideation assistant.

Generate exactly {num_ideas} distinct early-stage venture concepts for the
following domain:

{domain}

Additional direction from the study operator:
{direction_block}

Your task is to propose diverse, original, and testable venture concepts.
Each concept must contain:
- a specific target user;
- one concrete problem or desire;
- one distinctive core concept;
- a plausible approach;
- a narrowly scoped prototype.

Requirements:
- Generate exactly {num_ideas} ideas.
- Keep every idea meaningfully different from the others in at least two of:
  target user, problem/desire, value proposition, core interaction mechanism,
  delivery model, or prototype approach.
- Aim for original and non-obvious concepts, but do not treat speculative
  mechanisms as established facts.
- Do not include web search, citations, market statistics, company names,
  competitor claims, patent claims, or assertions of market validation.
- Do not rank, filter, critique, score, or improve the generated ideas.
- Do not describe a concept as guaranteed to work or proven to succeed.
- Avoid illegal, deceptive, exploitative, or privacy-invasive concepts.
- All JSON values must be written in English.
- Keep JSON keys in English.
- Return valid JSON only, with no Markdown and no prose outside JSON.

Required output schema:

{{
  "metadata": {{
    "prompt_regime": "{P1_PROMPT_REGIME}",
    "prompt_version": "{P1_VERSION}",
    "domain": "{domain}",
    "language": "en"
  }},
  "ideas": [
    {{
      "idea_id": "I001",
      "domain": "{domain}",
      "title": "",
      "target_user": "",
      "problem_or_desire": "",
      "core_concept": "",
      "proposed_approach": "",
      "prototype_scope": ""
    }}
  ]
}}
""".strip()


# ---------------------------------------------------------------------------
# Normalization for compatibility with existing CHVD M3 filter
# ---------------------------------------------------------------------------

def first_nonempty(source: Dict[str, Any], *keys: str, default: Any = "") -> Any:
    for key in keys:
        value = source.get(key)
        if value not in (None, "", [], {}):
            return value
    return default


def normalize_to_legacy_chvd_schema(
    *,
    raw_idea: Dict[str, Any],
    index: int,
    domain: str,
) -> Dict[str, Any]:
    """
    Preserve compact P1 fields AND provide legacy keys used by the existing
    M3 v5.4 filter and downstream blind-evaluation preparation code.
    """
    supplied_idea_id = str(
        first_nonempty(raw_idea, "idea_id", "candidate_id", default="")
    ).strip()

    title = str(
        first_nonempty(
            raw_idea,
            "title",
            "idea_name",
            "name",
            default=f"Ý tưởng {index}",
        )
    ).strip()

    target_user = str(
        first_nonempty(raw_idea, "target_user", default="")
    ).strip()

    problem_or_desire = str(
        first_nonempty(
            raw_idea,
            "problem_or_desire",
            "hidden_pain_or_desire",
            "pain_point",
            "problem",
            default="",
        )
    ).strip()

    core_concept = str(
        first_nonempty(
            raw_idea,
            "core_concept",
            "creative_leap",
            "one_sentence_pitch",
            default="",
        )
    ).strip()

    proposed_approach = str(
        first_nonempty(
            raw_idea,
            "proposed_approach",
            "proposed_solution",
            "solution",
            default="",
        )
    ).strip()

    prototype_scope = str(
        first_nonempty(
            raw_idea,
            "prototype_scope",
            "mvp_seed",
            "potential_mvp",
            "mvp",
            default="",
        )
    ).strip()

    # Sequential IDs prevent duplicate/malformed LLM IDs from corrupting M3 mapping.
    idea_id = f"I{index:03d}"

    return {
        # P1 compact contract
        "idea_id": idea_id,
        "source_idea_id": supplied_idea_id,
        "domain": str(first_nonempty(raw_idea, "domain", default=domain)).strip(),
        "title": title,
        "target_user": target_user,
        "problem_or_desire": problem_or_desire,
        "core_concept": core_concept,
        "proposed_approach": proposed_approach,
        "prototype_scope": prototype_scope,

        # Legacy CHVD contract required by the existing M3 filter
        "idea_name": title,
        "one_sentence_pitch": core_concept,
        "hidden_pain_or_desire": problem_or_desire,
        "creative_leap": core_concept,
        "non_obvious_connection": "",
        "proposed_solution": proposed_approach,
        "core_experience": core_concept,
        "core_technology": [],
        "why_it_feels_new": "",
        "why_now_speculative": "",
        "mvp_seed": prototype_scope,
        "first_7_day_prototype": prototype_scope,
        "business_model_hypothesis": "",
        "wildness_level": None,
        "main_assumptions": [],
        "main_risks": [],
        "validation_needed_later": [],
        "hallucination_notes": {
            "intentionally_speculative_parts": [],
            "claims_not_yet_validated": [],
        },
    }


def validate_and_normalize_output(
    *,
    parsed: Optional[Dict[str, Any]],
    domain: str,
    expected_count: int,
    allow_count_mismatch: bool,
) -> Dict[str, Any]:
    if not isinstance(parsed, dict):
        raise ValueError(
            "Could not recover a valid top-level JSON object containing 'ideas'."
        )

    raw_ideas = parsed.get("ideas")
    if not isinstance(raw_ideas, list):
        raise ValueError(
            "Generator output lacks a valid top-level 'ideas' list."
        )

    ideas = [
        normalize_to_legacy_chvd_schema(
            raw_idea=item,
            index=index,
            domain=domain,
        )
        for index, item in enumerate(raw_ideas, start=1)
        if isinstance(item, dict)
    ]

    if not ideas:
        raise ValueError("No valid idea objects were found in the response.")

    if len(ideas) != expected_count:
        message = (
            f"Expected exactly {expected_count} ideas but recovered {len(ideas)}. "
            "Do not use an uneven output for a controlled M0/M1 experiment."
        )
        if not allow_count_mismatch:
            raise ValueError(message + " Re-run the generator.")
        print("[WARN] " + message + " Proceeding because --allow-count-mismatch was set.")

    metadata = parsed.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}

    metadata.update(
        {
            "pipeline": "CHVD",
            "prompt_regime": P1_PROMPT_REGIME,
            "prompt_version": P1_VERSION,
            "stage": "shared_neutral_venture_ideation_generator",
            "domain": domain,
            "language": "vi",
            "schema_contract": "P1_compact_plus_legacy_CHVD_aliases",
            "normalization_policy": (
                "Sequential idea_id values are assigned by the script after "
                "generation to protect downstream M3 mapping."
            ),
            "saved_at": now_iso(),
        }
    )

    return {"metadata": metadata, "ideas": ideas}


def check_condition_consistency(condition: str, temperature: float, top_p: float) -> None:
    if condition == "M0":
        if temperature > 0.2 or top_p > 0.2:
            print(
                "[WARN] Condition M0 normally uses temperature <= 0.2 and "
                "top-p <= 0.2. Your parameters differ."
            )
    elif condition == "M1":
        if temperature < 1.0 or top_p < 0.8:
            print(
                "[WARN] Condition M1 normally uses temperature >= 1.0 and "
                "top-p >= 0.8. Your parameters differ."
            )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "CHVD P1 shared neutral generator. Use the same prompt for "
            "M0 and M1; vary only sampling parameters."
        )
    )
    parser.add_argument("--domain", required=True, type=str)
    parser.add_argument("--num-ideas", required=True, type=int)
    parser.add_argument(
        "--condition",
        choices=["M0", "M1", "TEST"],
        default="TEST",
        help="Metadata only. It does not alter the prompt.",
    )
    parser.add_argument(
        "--model",
        type=str,
        default="gemini-2.5-flash-lite",
    )
    parser.add_argument("--temperature", type=float, required=True)
    parser.add_argument("--top-p", type=float, required=True)
    parser.add_argument("--max-output-tokens", type=int, default=8192)
    parser.add_argument("--retries", type=int, default=5)
    parser.add_argument("--retry-sleep-base", type=float, default=2.0)
    parser.add_argument(
        "--no-json-repair",
        action="store_true",
        help="Disable syntax-only JSON repair when the first response is malformed.",
    )
    parser.add_argument(
        "--allow-count-mismatch",
        action="store_true",
        help="Not recommended for controlled experiments.",
    )
    parser.add_argument(
        "--user-prompt",
        type=str,
        default="",
        help=(
            "Optional shared direction. Keep it identical for M0 and M1 "
            "within a comparison cell."
        ),
    )
    parser.add_argument(
        "--prompt-file",
        type=str,
        default="",
        help="Optional UTF-8 text file containing the shared direction.",
    )
    parser.add_argument(
        "--api-key-env",
        type=str,
        default="GEMINI_GENERATOR_KEY",
    )
    parser.add_argument("--api-key", type=str, default="")
    parser.add_argument("--out-dir", type=str, default="outputs_P1")
    parser.add_argument("--out-file", type=str, default="ideas_raw.json")
    args = parser.parse_args()

    if args.num_ideas <= 0:
        raise ValueError("--num-ideas must be positive.")
    if not 0.0 <= args.temperature <= 2.0:
        raise ValueError("--temperature must be within [0.0, 2.0].")
    if not 0.0 < args.top_p <= 1.0:
        raise ValueError("--top-p must be within (0.0, 1.0].")

    api_key = args.api_key.strip() or os.environ.get(args.api_key_env, "").strip()
    if not api_key:
        raise RuntimeError(
            f"Missing API key. In PowerShell ru[LOCAL_PATH_REDACTED]"
            f'$env:{args.api_key_env}="YOUR_API_KEY"'
        )

    user_direction = args.user_prompt.strip()
    if args.prompt_file:
        user_direction = Path(args.prompt_file).read_text(
            encoding="utf-8"
        ).strip()

    check_condition_consistency(args.condition, args.temperature, args.top_p)

    prompt = build_p1_prompt(
        domain=args.domain,
        num_ideas=args.num_ideas,
        user_direction=user_direction,
    )

    out_dir = ensure_dir(args.out_dir)
    output_path = out_dir / args.out_file
    prompt_debug_path = out_dir / "generator_prompt_debug.txt"
    raw_debug_path = out_dir / "generator_raw_response_debug.txt"
    repair_debug_path = out_dir / "generator_repaired_response_debug.txt"

    prompt_debug_path.write_text(prompt, encoding="utf-8")

    print("[INFO] CHVD P1 Shared Neutral Generator")
    print(f"[INFO] Condition label: {args.condition}")
    print(f"[INFO] Model: {args.model}")
    print(f"[INFO] Temperature: {args.temperature}")
    print(f"[INFO] Top-p: {args.top_p}")
    print(f"[INFO] Domain: {args.domain}")
    print(f"[INFO] Requested ideas: {args.num_ideas}")

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

    parsed = extract_json(raw_text)

    if parsed is None and not args.no_json_repair:
        print("[WARN] Initial response was not valid JSON. Running syntax-only repair.")
        repaired_text = repair_json_with_gemini(
            api_key=api_key,
            model=args.model,
            raw_text=raw_text,
            max_output_tokens=args.max_output_tokens,
            retries=args.retries,
            retry_sleep_base=args.retry_sleep_base,
        )
        repair_debug_path.write_text(repaired_text, encoding="utf-8")
        parsed = extract_json(repaired_text)

    normalized = validate_and_normalize_output(
        parsed=parsed,
        domain=args.domain,
        expected_count=args.num_ideas,
        allow_count_mismatch=args.allow_count_mismatch,
    )

    normalized["metadata"].update(
        {
            "condition_label": args.condition,
            "generator_model": args.model,
            "generator_temperature": args.temperature,
            "generator_top_p": args.top_p,
            "user_prompt": user_direction,
            "max_output_tokens": args.max_output_tokens,
        }
    )

    output_path.write_text(
        json.dumps(normalized, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"[OK] Generated {len(normalized['ideas'])} ideas.")
    print(f"[OK] Output JSON: {output_path.resolve()}")
    print(f"[OK] Prompt debug: {prompt_debug_path.resolve()}")
    print(f"[OK] Raw response debug: {raw_debug_path.resolve()}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[INFO] Interrupted by user.")
        sys.exit(130)
