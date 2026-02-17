from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

try:
    from google import genai
    from google.genai import types
except ImportError as exc:  # pragma: no cover
    raise RuntimeError(
        "google-genai is required. Please install dependencies from requirements.txt"
    ) from exc


def _strip_code_fence(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```[a-zA-Z0-9_\-]*\n", "", stripped)
        stripped = re.sub(r"\n```$", "", stripped)
    return stripped.strip()


def _extract_json(text: str) -> dict[str, Any]:
    candidate = _strip_code_fence(text)
    try:
        parsed = json.loads(candidate)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    start = candidate.find("{")
    end = candidate.rfind("}")
    if start >= 0 and end > start:
        maybe_json = candidate[start : end + 1]
        parsed = json.loads(maybe_json)
        if isinstance(parsed, dict):
            return parsed

    raise RuntimeError("Gemini did not return valid JSON.")


def _normalize_timestamp(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "00:00:00"

    if raw.isdigit():
        total = int(raw)
        return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"

    match = re.search(r"(\d{1,2}:\d{2}:\d{2}|\d{1,2}:\d{2})", raw)
    if match:
        raw = match.group(1)

    parts = raw.split(":")
    if len(parts) == 2 and all(p.isdigit() for p in parts):
        minute, second = (int(parts[0]), int(parts[1]))
        total = minute * 60 + second
        return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"

    if len(parts) == 3 and all(p.isdigit() for p in parts):
        hour, minute, second = (int(parts[0]), int(parts[1]), int(parts[2]))
        return f"{hour:02d}:{minute:02d}:{second:02d}"

    return "00:00:00"


def normalize_article_plan(raw: dict[str, Any], max_images: int | None) -> dict[str, Any]:
    title = str(raw.get("title") or "未命名文章标题").strip()
    lead = str(raw.get("lead") or "").strip()
    conclusion = str(raw.get("conclusion") or "").strip()
    tags = raw.get("tags") or []
    if not isinstance(tags, list):
        tags = []
    tags = [str(item).strip() for item in tags if str(item).strip()]

    normalized_sections: list[dict[str, Any]] = []
    image_counter = 0

    for section in raw.get("sections", []):
        if not isinstance(section, dict):
            continue

        heading = str(section.get("heading") or "小节").strip()
        body_markdown = str(
            section.get("body_markdown")
            or section.get("body")
            or section.get("content")
            or ""
        ).strip()

        image_payload = section.get("image") if isinstance(section.get("image"), dict) else {}
        need_image = bool(image_payload.get("need"))

        normalized_image = {
            "need": False,
            "timestamp": "00:00:00",
            "caption": "",
            "alt": "",
            "anchor": "",
        }

        can_use_image = max_images is None or image_counter < max_images
        if need_image and can_use_image:
            image_counter += 1
            caption = str(image_payload.get("caption") or heading).strip()
            alt = str(image_payload.get("alt") or caption).strip()
            normalized_image = {
                "need": True,
                "timestamp": _normalize_timestamp(image_payload.get("timestamp")),
                "caption": caption,
                "alt": alt,
                "anchor": str(image_payload.get("anchor") or "").strip(),
            }

        normalized_sections.append(
            {
                "heading": heading,
                "body_markdown": body_markdown,
                "image": normalized_image,
            }
        )

    if not normalized_sections:
        normalized_sections = [
            {
                "heading": "核心内容",
                "body_markdown": lead or "请根据视频内容补充正文。",
                "image": {
                    "need": False,
                    "timestamp": "00:00:00",
                    "caption": "",
                    "alt": "",
                    "anchor": "",
                },
            }
        ]

    return {
        "title": title,
        "lead": lead,
        "sections": normalized_sections,
        "conclusion": conclusion,
        "tags": tags,
    }


def build_generation_prompt(
    transcript_text: str,
    user_instruction: str,
    target_words: int,
    max_images: int | None,
) -> str:
    image_limit_rule = (
        f"配图总数不能超过 {max_images} 张。"
        if isinstance(max_images, int)
        else "配图数量不固定，按内容需要决定，只在真正需要视觉辅助的段落配图。"
    )
    return f"""
你是一个专业的中文科技作者和编辑。请基于给定视频逐字稿，产出一篇可发布的中文 Markdown 文章规划。

任务要求：
1. 按用户要求写作，风格自然、结构清晰。
2. 全文目标字数约 {target_words} 字（允许上下浮动 10%）。
3. 自动判断哪些段落适合配图。{image_limit_rule}
4. 每张图必须给出视频时间戳（timestamp），用于后续自动截图。
5. image.anchor 必须是 body_markdown 中原样出现的一段连续文本，长度 8-40 字符，用于插图锚点。
6. 只输出 JSON，不要输出额外解释。

JSON 格式：
{{
  "title": "文章标题",
  "lead": "导语（1-2段）",
  "sections": [
    {{
      "heading": "小节标题",
      "body_markdown": "小节正文（Markdown）",
      "image": {{
        "need": true,
        "timestamp": "HH:MM:SS",
        "caption": "图片说明",
        "alt": "图片ALT",
        "anchor": "正文中的连续文本"
      }}
    }}
  ],
  "conclusion": "结语",
  "tags": ["标签1", "标签2"]
}}

用户写作要求：
{user_instruction}

视频逐字稿（带时间戳）：
{transcript_text}
""".strip()


def _request_gemini(api_key: str, model_name: str, prompt: str) -> str:
    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=model_name,
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0.4,
            response_mime_type="application/json",
        ),
    )

    text = getattr(response, "text", "")
    if text:
        return text

    raise RuntimeError("Gemini returned empty response.")


def generate_article_plan(
    transcript_text: str,
    user_instruction: str,
    model_name: str,
    target_words: int,
    max_images: int | None = None,
) -> dict[str, Any]:
    env_path = Path(__file__).resolve().parent / ".env"
    load_dotenv(dotenv_path=env_path, override=False)
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("Missing GEMINI_API_KEY in environment.")

    prompt = build_generation_prompt(
        transcript_text=transcript_text,
        user_instruction=user_instruction,
        target_words=target_words,
        max_images=max_images,
    )

    raw_text = _request_gemini(
        api_key=api_key,
        model_name=model_name,
        prompt=prompt,
    )

    raw = _extract_json(raw_text)
    return normalize_article_plan(raw, max_images=max_images)
