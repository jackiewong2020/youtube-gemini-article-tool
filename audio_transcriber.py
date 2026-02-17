from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from yt_dlp import YoutubeDL

try:
    from google import genai
    from google.genai import types
except ImportError as exc:  # pragma: no cover
    raise RuntimeError(
        "google-genai is required. Please install dependencies from requirements.txt"
    ) from exc


def transcribe_video_audio_with_gemini(youtube_url: str) -> list[dict[str, Any]]:
    env_path = Path(__file__).resolve().parent / ".env"
    load_dotenv(dotenv_path=env_path, override=False)

    if not _is_enabled(os.getenv("GEMINI_TRANSCRIBE_FALLBACK", "true")):
        return []

    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        return []

    model_name = os.getenv("GEMINI_TRANSCRIBE_MODEL", "gemini-2.5-flash").strip()
    if not model_name:
        return []

    client = genai.Client(api_key=api_key)
    prompt = _build_transcribe_prompt()

    segments = _transcribe_by_uri(client, model_name, youtube_url, prompt)
    if segments:
        return segments

    return _transcribe_by_uploaded_audio(client, model_name, youtube_url, prompt)


def _transcribe_by_uri(
    client: genai.Client,
    model_name: str,
    youtube_url: str,
    prompt: str,
) -> list[dict[str, Any]]:
    try:
        response = client.models.generate_content(
            model=model_name,
            contents=[
                types.Content(
                    parts=[
                        types.Part(file_data=types.FileData(file_uri=youtube_url)),
                        types.Part(text=prompt),
                    ]
                )
            ],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
            ),
        )
    except Exception:
        return []

    return _parse_transcribe_response(getattr(response, "text", ""))


def _transcribe_by_uploaded_audio(
    client: genai.Client,
    model_name: str,
    youtube_url: str,
    prompt: str,
) -> list[dict[str, Any]]:
    uploaded = None
    try:
        with tempfile.TemporaryDirectory(prefix="yt_audio_") as tmp:
            audio_file = _download_audio_file(youtube_url, Path(tmp))
            uploaded = client.files.upload(file=str(audio_file))
            response = client.models.generate_content(
                model=model_name,
                contents=[prompt, uploaded],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                ),
            )
            return _parse_transcribe_response(getattr(response, "text", ""))
    except Exception:
        return []
    finally:
        if uploaded and getattr(uploaded, "name", ""):
            try:
                client.files.delete(name=uploaded.name)
            except Exception:
                pass


def _download_audio_file(youtube_url: str, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    options = {
        "outtmpl": str(output_dir / "%(id)s.%(ext)s"),
        "format": "bestaudio/best",
        "quiet": True,
        "noprogress": True,
        "no_warnings": True,
    }
    with YoutubeDL(options) as ydl:
        info = ydl.extract_info(youtube_url, download=True)

    video_id = str(info.get("id", "")).strip()
    if not video_id:
        raise RuntimeError("Unable to download audio for Gemini transcription.")

    candidates = sorted(
        output_dir.glob(f"{video_id}.*"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise RuntimeError("Audio file not found after yt-dlp download.")
    return candidates[0]


def _build_transcribe_prompt() -> str:
    return """
请把音频内容转成结构化 JSON，并严格输出为 JSON。

输出格式：
{
  "segments": [
    {
      "timestamp": "HH:MM:SS",
      "text": "该时间点对应的转写文本"
    }
  ]
}

要求：
1. 按时间顺序输出。
2. 每段保持简洁、语义完整。
3. timestamp 必须可解析（HH:MM:SS 或 MM:SS）。
4. 不要输出任何 JSON 之外的说明文字。
""".strip()


def _parse_transcribe_response(text: str) -> list[dict[str, Any]]:
    payload = _extract_json_payload(text)
    if not payload:
        return []

    raw_segments = payload.get("segments")
    if not isinstance(raw_segments, list):
        return []

    parsed: list[dict[str, Any]] = []
    for item in raw_segments:
        if not isinstance(item, dict):
            continue
        timestamp_raw = (
            item.get("timestamp")
            or item.get("time")
            or item.get("start")
            or item.get("start_time")
            or ""
        )
        text_raw = item.get("text") or item.get("content") or item.get("transcript") or ""
        segment_text = str(text_raw).replace("\n", " ").strip()
        if not segment_text:
            continue
        start_seconds = _parse_timestamp_to_seconds(timestamp_raw)
        parsed.append(
            {
                "start": start_seconds,
                "duration": 0.0,
                "text": segment_text,
            }
        )

    if not parsed:
        return []

    parsed.sort(key=lambda item: item["start"])
    for index, item in enumerate(parsed):
        if index + 1 < len(parsed):
            item["duration"] = max(0.0, parsed[index + 1]["start"] - item["start"])
        else:
            item["duration"] = 8.0
    return parsed


def _extract_json_payload(text: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    if not raw:
        return {}

    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z0-9_-]*\n", "", raw)
        raw = re.sub(r"\n```$", "", raw).strip()

    for candidate in (raw, _substring_json(raw)):
        if not candidate:
            continue
        try:
            obj = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
        if isinstance(obj, list):
            return {"segments": obj}
    return {}


def _substring_json(text: str) -> str:
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return text[start : end + 1]
    start = text.find("[")
    end = text.rfind("]")
    if start >= 0 and end > start:
        return text[start : end + 1]
    return ""


def _parse_timestamp_to_seconds(value: Any) -> float:
    raw = str(value or "").strip()
    if not raw:
        return 0.0

    if raw.isdigit():
        return float(int(raw))

    match = re.search(r"(\d{1,2}:\d{2}:\d{2}|\d{1,2}:\d{2})", raw)
    if match:
        raw = match.group(1)

    parts = raw.split(":")
    if len(parts) == 2 and all(_is_number(piece) for piece in parts):
        minute = int(float(parts[0]))
        second = float(parts[1])
        return minute * 60 + second
    if len(parts) == 3 and all(_is_number(piece) for piece in parts):
        hour = int(float(parts[0]))
        minute = int(float(parts[1]))
        second = float(parts[2])
        return hour * 3600 + minute * 60 + second
    return 0.0


def _is_enabled(value: str) -> bool:
    return str(value).strip().lower() not in {"0", "false", "off", "no"}


def _is_number(value: str) -> bool:
    try:
        float(value)
    except ValueError:
        return False
    return True
