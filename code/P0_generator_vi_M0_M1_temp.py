#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
01_generator_v5_3_creative_hallucination_json_repair.py

CHVD v5.3 - Generator tầng 1:
    HIGH-TEMPERATURE CREATIVE HALLUCINATION GENERATOR

Mục tiêu:
    - Sinh ý tưởng khởi nghiệp công nghệ thật sáng tạo, bay, dị, không rập khuôn.
    - Không dùng corpus.
    - Không dùng RAG.
    - Không dùng weak signals.
    - Không bắt buộc có evidence ở bước này.
    - Output tiếng Việt theo JSON schema ổn định.

Triết lý:
    Tầng này KHÔNG phải để kiểm chứng.
    Tầng này là để tạo ra nhiều "giả thuyết cơ hội" táo bạo.
    Việc lọc, chấm nghiêm túc, giảm hallucination sẽ do file 2 đảm nhiệm.

Cài thư viện:
    pip install -U google-genai

Set API key trên PowerShell:
    $env:GEMINI_GENERATOR_KEY="API_KEY_THU_NHAT"

Ví dụ chạy:
    python 01_generator_v5_3_creative_hallucination_json_repair.py `
      --domain "Game Mobile or Software App" `
      --num-ideas 10 `
      --model "gemini-2.5-flash-lite" `
      --temperature 2.0 `
      --top-p 0.99 `
      --max-output-tokens 8192

Output:
    outputs_v5_3/ideas_raw_vi.json
    outputs_v5_3/generator_raw_response_debug.txt
"""

from __future__ import annotations

import argparse
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


IDEA_LIKE_KEYS = {
    "idea_id",
    "idea_name",
    "one_sentence_pitch",
    "target_user",
    "hidden_pain_or_desire",
    "creative_leap",
    "non_obvious_connection",
    "proposed_solution",
    "core_experience",
    "mvp_seed",
    "first_7_day_prototype",
    "business_model_hypothesis",
}


def safe_json_loads(text: str) -> Optional[Any]:
    """
    JSON parser mềm hơn json.loads mặc định.
    """
    if not isinstance(text, str):
        return None
    try:
        return json.loads(text)
    except Exception:
        try:
            return json.JSONDecoder(strict=False).decode(text)
        except Exception:
            return None


def strip_json_fence(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def is_idea_like_dict(obj: Any) -> bool:
    if not isinstance(obj, dict):
        return False
    keys = set(obj.keys())
    return len(keys & IDEA_LIKE_KEYS) >= 2


def is_idea_like_list(obj: Any) -> bool:
    if not isinstance(obj, list) or not obj:
        return False
    dict_items = [x for x in obj if isinstance(x, dict)]
    if not dict_items:
        return False
    idea_like_count = sum(1 for x in dict_items if is_idea_like_dict(x))
    return idea_like_count >= max(1, len(dict_items) // 2)


def iter_balanced_json_candidates(text: str) -> List[str]:
    """
    Trả về các substring JSON cân bằng.
    Ưu tiên object/array lớn trước để tránh nhặt nhầm object con.
    """
    candidates: List[str] = []
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
                        candidates.append(text[start:i + 1])
                        break
                else:
                    break

    candidates.sort(key=len, reverse=True)
    return candidates


def recover_metadata_and_ideas(text: str) -> Optional[Dict[str, Any]]:
    """
    Cứu output khi có object/list JSON hợp lệ nằm trong response.
    Không bao giờ nhận list con như main_assumptions vì chúng không idea-like.
    """
    for candidate in iter_balanced_json_candidates(text):
        parsed = safe_json_loads(candidate)
        if isinstance(parsed, dict) and is_idea_like_list(parsed.get("ideas")):
            return parsed
        if is_idea_like_list(parsed):
            return {
                "metadata": {"schema_recovered_from": "top_level_idea_list"},
                "ideas": parsed,
            }

    return None


def extract_json(text: str, fallback: Any = None) -> Any:
    """
    Trích JSON nhưng ưu tiên object cha có 'ideas'.
    Không nhặt metadata hoặc main_assumptions làm output chính.
    """
    if not text:
        return fallback

    text = strip_json_fence(text)

    direct = safe_json_loads(text)
    if direct is not None:
        if isinstance(direct, dict) and is_idea_like_list(direct.get("ideas")):
            return direct
        if is_idea_like_list(direct):
            return {
                "metadata": {"schema_recovered_from": "top_level_idea_list"},
                "ideas": direct,
            }
        return fallback

    recovered = recover_metadata_and_ideas(text)
    if recovered is not None:
        return recovered

    return fallback


def repair_json_with_gemini(
    api_key: str,
    model: str,
    raw_text: str,
    max_output_tokens: int,
    retries: int,
    retry_sleep_base: float,
) -> str:
    """
    Khi model high-temperature sinh JSON malformed do dấu quote chưa escape,
    dùng một lượt temperature=0 để repair JSON, không sinh ý tưởng mới.
    """
    repair_prompt = f"""
Bạn là JSON repairer. Nhiệm vụ duy nhất: sửa raw text dưới đây thành JSON hợp lệ.

Quy tắc bắt buộc:
- Không sinh ý tưởng mới.
- Không thêm nội dung mới.
- Không bỏ idea nếu có thể giữ.
- Chỉ sửa lỗi JSON syntax: escape dấu ngoặc kép bên trong string, bỏ trailing comma, đóng ngoặc còn thiếu nếu thấy rõ.
- Output chỉ là JSON object hợp lệ.
- JSON object phải có top-level keys: "metadata" và "ideas".
- "ideas" phải là list các idea object.
- Nếu có chuỗi chứa dấu ngoặc kép bên trong value, hãy đổi dấu ngoặc kép nội bộ thành dấu nháy đơn hoặc escape bằng \\\".
- Không markdown.

RAW TEXT CẦN SỬA:
{raw_text}
""".strip()

    print("[WARN] Raw generator response không parse được JSON chuẩn.")
    print("[WARN] Đang chạy JSON repair bằng cùng model ở temperature=0...")

    repaired = call_gemini(
        api_key=api_key,
        model=model,
        prompt=repair_prompt,
        temperature=0.0,
        top_p=0.1,
        max_output_tokens=max_output_tokens,
        retries=retries,
        retry_sleep_base=retry_sleep_base,
    )
    return repaired


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
    """
    Gọi Gemini API với retry.
    Bản v5.2 khôi phục hàm này vì v5.1 robust parser đã vô tình thiếu.
    """
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
# Prompt Generator
# =============================================================================

def build_generator_prompt(domain: str, num_ideas: int, user_prompt: str = "") -> str:
    direction = user_prompt.strip()
    if not direction:
        direction = (
            "Hãy tự do khám phá các hướng startup cực kỳ sáng tạo, "
            "khác thường, mới lạ, nhưng vẫn có một hạt nhân MVP có thể thử nghiệm."
        )

    return f"""
Vai trò của bạn:
Bạn là một AI sáng tạo cực cao, chuyên sinh ra các "giả thuyết cơ hội khởi nghiệp" mới mẻ.
Bạn không phải nhà kiểm chứng thị trường.
Bạn không phải nhà đầu tư thận trọng.
Bạn không phải filter.
Bạn là nguồn tạo ý tưởng bay, lạ, khác khuôn mẫu.

Mục tiêu:
Sinh đúng {num_ideas} ý tưởng khởi nghiệp trong lĩnh vực:

{domain}

Yêu cầu định hướng từ người dùng:

{direction}

Nguyên tắc sáng tạo:
- Hãy suy nghĩ như một founder đầy triển vọng muốn tìm đến các hướng khởi nghiệp mới lạ.
- Ưu tiên ý tưởng kỳ lạ.
- Ưu tiên sự kết hợp không hiển nhiên giữa nhiều lĩnh vực.
- Ưu tiên blue-ocean, niche market, hidden pain, underexplored behavior.
- Không được chỉ đưa ra ý tưởng phổ thông.
- Không được tự làm ý tưởng rập khuôn.
- Không cần có evidence ở bước này.
- Không cần kiểm chứng thị trường ở bước này.
- Không cần chứng minh đối thủ chưa tồn tại ở bước này.
- Không được bịa citation, số liệu, tên công ty, nguồn nghiên cứu, patent.
- Nếu có claim chưa chắc, hãy đưa vào hallucination_notes thay vì giả vờ chắc chắn.
- Không sinh ý tưởng trái pháp luật, lừa đảo, gây hại, xâm phạm quyền riêng tư, hoặc khai thác nhóm yếu thế.
- Toàn bộ nội dung value phải bằng tiếng Việt.
- Tên key giữ tiếng Anh để dễ parse.
- Chỉ trả về JSON hợp lệ.
- Không giải thích ngoài JSON.

Bắt buộc:
- Top-level JSON phải là object.
- Phải có key "metadata" và "ideas".
- Key "ideas" phải là list.
- Mỗi idea phải có idea_id dạng I001, I002, ...

Schema bắt buộc:

{{
  "metadata": {{
    "pipeline": "CHVD",
    "version": "v5.3",
    "domain": "{domain}",
    "language": "vi",
    "mode": "prompt_only_no_corpus_no_weak_signals",
    "temperature_role": "maximal_divergent_creativity",
  }},
  "ideas": [
    {{
      "idea_id": "I001",
      "idea_name": "",
      "domain": "",
      "one_sentence_pitch": "",
      "target_user": "",
      "hidden_pain_or_desire": "",
      "creative_leap": "",
      "non_obvious_connection": "",
      "proposed_solution": "",
      "core_experience": "",
      "core_technology": [
        ""
      ],
      "why_it_feels_new": "",
      "why_now_speculative": "",
      "mvp_seed": "",
      "first_7_day_prototype": "",
      "business_model_hypothesis": "",
      "wildness_level": 1,
      "main_assumptions": [
        {{
          "claim": "",
          "type": "user_need"
        }},
        {{
          "claim": "",
          "type": "technical_plausibility"
        }},
        {{
          "claim": "",
          "type": "business_potential"
        }}
      ],
      "main_risks": [
        ""
      ],
      "validation_needed_later": [
        ""
      ],
      "hallucination_notes": {{
        "intentionally_speculative_parts": [
          ""
        ],
        "claims_not_yet_validated": [
          ""
        ]
      }}
    }}
  ]
}}

Chú ý:
- wildness_level là số 1-5:
  1 = khá an toàn,
  3 = sáng tạo vừa,
  5 = rất mới lạ, khác thường.
- Vì đây là generator sáng tạo, hãy ưu tiên wildness_level 3-5.
""".strip()


# =============================================================================
# Normalize output
# =============================================================================

def normalize_idea(idea: Dict[str, Any], idx: int, domain: str) -> Dict[str, Any]:
    def first(*keys: str, default: Any = "") -> Any:
        for k in keys:
            v = idea.get(k)
            if v not in (None, "", [], {}):
                return v
        return default

    normalized = {
        "idea_id": first("idea_id", default=f"I{idx:03d}"),
        "idea_name": first("idea_name", "name", "ten_y_tuong", default=f"Ý tưởng {idx}"),
        "domain": first("domain", default=domain),
        "one_sentence_pitch": first("one_sentence_pitch", "pitch", default=""),
        "target_user": first("target_user", "nguoi_dung_muc_tieu", default=""),
        "hidden_pain_or_desire": first("hidden_pain_or_desire", "pain_point", "problem", "van_de", default=""),
        "creative_leap": first("creative_leap", default=""),
        "non_obvious_connection": first("non_obvious_connection", default=""),
        "proposed_solution": first("proposed_solution", "solution", "giai_phap", default=""),
        "core_experience": first("core_experience", default=""),
        "core_technology": first("core_technology", "technology", "cong_nghe_loi", default=[]),
        "why_it_feels_new": first("why_it_feels_new", "novelty_claim", default=""),
        "why_now_speculative": first("why_now_speculative", "why_now", default=""),
        "mvp_seed": first("mvp_seed", "potential_mvp", "mvp", default=""),
        "first_7_day_prototype": first("first_7_day_prototype", default=""),
        "business_model_hypothesis": first("business_model_hypothesis", "business_model", default=""),
        "wildness_level": first("wildness_level", default=3),
        "main_assumptions": first("main_assumptions", "assumptions", default=[]),
        "main_risks": first("main_risks", "risks", default=[]),
        "validation_needed_later": first("validation_needed_later", "validation_needed", default=[]),
        "hallucination_notes": first("hallucination_notes", default={
            "intentionally_speculative_parts": [],
            "claims_not_yet_validated": []
        }),
    }

    # Ép list fields.
    for key in ["core_technology", "main_assumptions", "main_risks", "validation_needed_later"]:
        if isinstance(normalized[key], str):
            normalized[key] = [normalized[key]]
        elif normalized[key] is None:
            normalized[key] = []

    # Ép wildness 1-5.
    try:
        normalized["wildness_level"] = int(float(normalized["wildness_level"]))
    except Exception:
        normalized["wildness_level"] = 3
    normalized["wildness_level"] = max(1, min(5, normalized["wildness_level"]))

    if not isinstance(normalized["hallucination_notes"], dict):
        normalized["hallucination_notes"] = {
            "intentionally_speculative_parts": [],
            "claims_not_yet_validated": []
        }

    return normalized


def validate_generator_output(obj: Any, domain: str) -> Dict[str, Any]:
    """
    Chấp nhận nhiều dạng output nhưng normalize về:
    {
      "metadata": {...},
      "ideas": [...]
    }
    """
    if isinstance(obj, list):
        obj = {
            "metadata": {
                "schema_recovered_from": "top_level_list",
            },
            "ideas": obj
        }

    if obj is None:
        raise ValueError(
            "Không tìm được JSON object hợp lệ có key 'ideas'. "
            "Raw response có thể bị cụt hoặc sai JSON. Hãy mở generator_raw_response_debug.txt, "
            "hoặc thử giảm --num-ideas / tăng --max-output-tokens / chạy lại."
        )

    if not isinstance(obj, dict):
        raise ValueError("Output không phải JSON object/list hợp lệ.")

    if "ideas" not in obj or not isinstance(obj.get("ideas"), list):
        candidate_keys = [
            "startup_ideas",
            "venture_ideas",
            "business_ideas",
            "opportunities",
            "results",
            "data",
            "items",
            "generated_ideas",
        ]

        recovered = None
        recovered_key = None

        for key in candidate_keys:
            val = obj.get(key)
            if isinstance(val, list):
                recovered = val
                recovered_key = key
                break

        if recovered is None:
            for key, val in obj.items():
                if isinstance(val, list) and all(isinstance(x, dict) for x in val):
                    recovered = val
                    recovered_key = key
                    break

        if recovered is not None:
            obj["ideas"] = recovered
            obj.setdefault("metadata", {})
            obj["metadata"]["schema_recovered_from"] = recovered_key

    if "ideas" not in obj or not isinstance(obj["ideas"], list):
        raise ValueError(f"Output thiếu key 'ideas'. Các key nhận được: {list(obj.keys())}")

    cleaned: List[Dict[str, Any]] = []
    for idx, idea in enumerate(obj["ideas"], start=1):
        if isinstance(idea, dict):
            cleaned.append(normalize_idea(idea, idx, domain))

    if not cleaned:
        raise ValueError("Không có idea object hợp lệ trong output.")

    obj["ideas"] = cleaned
    obj.setdefault("metadata", {})
    obj["metadata"].update({
        "pipeline": "CHVD",
        "version": "v5.3",
        "stage": "high_temperature_creative_hallucination_generator",
        "domain": domain,
        "language": "vi",
        "mode": "prompt_only_no_corpus_no_weak_signals",
        "saved_at": now_iso(),
    })
    return obj


# =============================================================================
# Main
# =============================================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description="CHVD v5.3 Generator: high-temperature creative hallucination, prompt-only."
    )
    parser.add_argument("--domain", type=str, default="Game Mobile or Software App")
    parser.add_argument("--num-ideas", type=int, default=10)
    parser.add_argument("--model", type=str, default="gemini-2.5-flash-lite")
    parser.add_argument("--temperature", type=float, default=2.0, help="Nên dùng cao: 1.5-2.0.")
    parser.add_argument("--top-p", type=float, default=0.99)
    parser.add_argument("--max-output-tokens", type=int, default=8192)
    parser.add_argument("--retries", type=int, default=5)
    parser.add_argument("--retry-sleep-base", type=float, default=2.0)
    parser.add_argument("--no-json-repair", action="store_true", help="Tắt bước repair JSON malformed bằng temperature=0.")
    parser.add_argument("--api-key-env", type=str, default="GEMINI_GENERATOR_KEY")
    parser.add_argument("--api-key", type=str, default="", help="Không khuyến nghị truyền trực tiếp.")
    parser.add_argument("--user-prompt", type=str, default="", help="Prompt định hướng tự do.")
    parser.add_argument("--prompt-file", type=str, default="", help="File .txt chứa prompt định hướng.")
    parser.add_argument("--out-dir", type=str, default="outputs_v5_3")
    parser.add_argument("--out-file", type=str, default="ideas_raw_vi.json")
    args = parser.parse_args()

    api_key = args.api_key.strip() or os.environ.get(args.api_key_env, "").strip()
    if not api_key:
        raise RuntimeError(
            f"Thiếu API key. Hãy set PowerShel[LOCAL_PATH_REDACTED]"
            f'$env:{args.api_key_env}="API_KEY_THU_NHAT"'
        )

    user_prompt = args.user_prompt.strip()
    if args.prompt_file:
        user_prompt = Path(args.prompt_file).read_text(encoding="utf-8").strip()

    out_dir = ensure_dir(args.out_dir)
    out_path = out_dir / args.out_file
    raw_debug_path = out_dir / "generator_raw_response_debug.txt"
    prompt_debug_path = out_dir / "generator_prompt_debug.txt"

    prompt = build_generator_prompt(
        domain=args.domain,
        num_ideas=args.num_ideas,
        user_prompt=user_prompt,
    )
    prompt_debug_path.write_text(prompt, encoding="utf-8")

    print("[INFO] CHVD v5.3 Generator")
    print("[INFO] Mode: prompt-only, no corpus, no weak signals")
    print(f"[INFO] Model: {args.model}")
    print(f"[INFO] Temperature: {args.temperature}")
    print(f"[INFO] Top-p: {args.top_p}")
    print(f"[INFO] Domain: {args.domain}")
    print(f"[INFO] Số ý tưởng: {args.num_ideas}")

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

    if parsed is None and not args.no_json_repair:
        repaired_text = repair_json_with_gemini(
            api_key=api_key,
            model=args.model,
            raw_text=raw_text,
            max_output_tokens=args.max_output_tokens,
            retries=args.retries,
            retry_sleep_base=args.retry_sleep_base,
        )
        repair_debug_path = out_dir / "generator_repaired_response_debug.txt"
        repair_debug_path.write_text(repaired_text, encoding="utf-8")
        parsed = extract_json(repaired_text, fallback=None)
        print(f"[OK] Repaired debug: {repair_debug_path.resolve()}")

    try:
        parsed = validate_generator_output(parsed, domain=args.domain)
    except Exception as e:
        print("[ERROR] Không parse/normalize được output generator.")
        print(f"[ERROR] {e}")
        print(f"[DEBUG] Raw response: {raw_debug_path.resolve()}")
        print("[DEBUG] 1200 ký tự đầu:")
        print((raw_text or "")[:1200])
        raise

    parsed["metadata"]["generator_model"] = args.model
    parsed["metadata"]["generator_temperature"] = args.temperature
    parsed["metadata"]["generator_top_p"] = args.top_p
    parsed["metadata"]["user_prompt"] = user_prompt

    out_path.write_text(json.dumps(parsed, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[OK] Đã sinh {len(parsed['ideas'])} ý tưởng.")
    print(f"[OK] JSON: {out_path.resolve()}")
    print(f"[OK] Raw debug: {raw_debug_path.resolve()}")


if __name__ == "__main__":
    main()
