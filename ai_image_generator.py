from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

try:
    from google import genai
    from google.genai import types
except ImportError as exc:  # pragma: no cover
    raise RuntimeError(
        "google-genai is required. Please install dependencies from requirements.txt"
    ) from exc


def _load_config(model_name: str | None = None) -> tuple[str, str]:
    env_path = Path(__file__).resolve().parent / ".env"
    load_dotenv(dotenv_path=env_path, override=False)

    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("Missing GEMINI_API_KEY in environment.")

    model = (model_name or "").strip() or os.getenv(
        "GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image"
    ).strip()
    if not model:
        raise RuntimeError("Missing GEMINI image model.")

    return api_key, model


def _build_prompt(
    article_title: str,
    heading: str,
    caption: str,
    body_markdown: str,
) -> str:
    body_snippet = " ".join(body_markdown.split())[:600]
    return f"""
你是专业编辑插画师，请为中文长文生成一张高质量配图。

文章标题：{article_title or "未命名文章"}
章节标题：{heading}
图片说明：{caption or heading}

章节摘要：
{body_snippet}

要求：
1. 16:9 横图，适合公众号和 WordPress。
2. 风格写实偏信息可视化，简洁、现代，不要廉价卡通感。
3. 不要任何文字、Logo、水印。
4. 画面主体清晰，突出本章节核心概念。
5. 避免畸形人脸和奇怪手部细节。
""".strip()


def _extract_image_bytes(response: object) -> bytes:
    candidates = getattr(response, "candidates", None)
    if candidates:
        for candidate in candidates:
            content = getattr(candidate, "content", None)
            parts = getattr(content, "parts", None) or []
            for part in parts:
                inline_data = getattr(part, "inline_data", None)
                if inline_data and getattr(inline_data, "mime_type", "").startswith("image/"):
                    data = getattr(inline_data, "data", b"")
                    if isinstance(data, bytes):
                        return data

    parts = getattr(response, "parts", None) or []
    for part in parts:
        inline_data = getattr(part, "inline_data", None)
        if inline_data and getattr(inline_data, "mime_type", "").startswith("image/"):
            data = getattr(inline_data, "data", b"")
            if isinstance(data, bytes):
                return data

    raise RuntimeError("Gemini image generation returned no image data.")


def generate_ai_section_image(
    heading: str,
    body_markdown: str,
    caption: str,
    output_path: Path,
    article_title: str = "",
    model_name: str | None = None,
) -> Path:
    api_key, model = _load_config(model_name=model_name)

    client = genai.Client(api_key=api_key)
    prompt = _build_prompt(
        article_title=article_title,
        heading=heading,
        caption=caption,
        body_markdown=body_markdown,
    )

    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_modalities=["IMAGE"],
            image_config=types.ImageConfig(aspect_ratio="16:9"),
        ),
    )
    image_bytes = _extract_image_bytes(response)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(image_bytes)
    return output_path
