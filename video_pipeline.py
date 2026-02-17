from __future__ import annotations

import html
import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from urllib.error import URLError
from urllib.request import Request, urlopen
from urllib.parse import parse_qs, urlparse

from audio_transcriber import transcribe_video_audio_with_gemini
from yt_dlp import YoutubeDL
from youtube_transcript_api import YouTubeTranscriptApi


@dataclass
class TranscriptSegment:
    start: float
    duration: float
    text: str


def extract_video_id(youtube_url: str) -> str:
    parsed = urlparse(youtube_url.strip())
    host = (parsed.hostname or "").lower()

    if host == "youtu.be":
        video_id = parsed.path.lstrip("/")
        if video_id:
            return video_id

    if "youtube.com" in host:
        if parsed.path == "/watch":
            video_id = parse_qs(parsed.query).get("v", [""])[0]
            if video_id:
                return video_id

        if parsed.path.startswith("/shorts/"):
            parts = [p for p in parsed.path.split("/") if p]
            if len(parts) >= 2:
                return parts[1]

        if parsed.path.startswith("/embed/"):
            parts = [p for p in parsed.path.split("/") if p]
            if len(parts) >= 2:
                return parts[1]

    raise ValueError(f"Cannot parse video id from URL: {youtube_url}")


def fetch_transcript(
    video_id: str,
    languages: Iterable[str] | None = None,
    youtube_url: str | None = None,
) -> list[TranscriptSegment]:
    preferred_langs = list(languages or ("zh-Hans", "zh-CN", "zh", "en"))

    try:
        api = YouTubeTranscriptApi()
        fetched = api.fetch(video_id, languages=preferred_langs)
        raw_segments = fetched.to_raw_data()
    except Exception as exc:
        fallback_segments = _fetch_transcript_via_ytdlp(video_id, preferred_langs)
        if fallback_segments:
            return fallback_segments
        if youtube_url:
            gemini_segments = _fetch_transcript_via_gemini(youtube_url)
            if gemini_segments:
                return gemini_segments
        raise RuntimeError(
            "Failed to fetch transcript. No subtitle track found and Gemini audio fallback did not return transcript."
        ) from exc

    segments: list[TranscriptSegment] = []
    for item in raw_segments:
        text = str(item.get("text", "")).replace("\n", " ").strip()
        if not text:
            continue
        segments.append(
            TranscriptSegment(
                start=float(item.get("start", 0.0)),
                duration=float(item.get("duration", 0.0)),
                text=text,
            )
        )
    if not segments:
        raise RuntimeError("Transcript is empty.")
    return segments


def _fetch_transcript_via_gemini(youtube_url: str) -> list[TranscriptSegment]:
    raw_segments = transcribe_video_audio_with_gemini(youtube_url)
    if not raw_segments:
        return []

    segments: list[TranscriptSegment] = []
    for item in raw_segments:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text", "")).replace("\n", " ").strip()
        if not text:
            continue
        segments.append(
            TranscriptSegment(
                start=float(item.get("start", 0.0)),
                duration=float(item.get("duration", 0.0)),
                text=text,
            )
        )
    return segments


def _fetch_transcript_via_ytdlp(
    video_id: str,
    preferred_langs: list[str],
) -> list[TranscriptSegment]:
    url = f"https://www.youtube.com/watch?v={video_id}"
    options = {
        "skip_download": True,
        "quiet": True,
        "no_warnings": True,
    }

    try:
        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception:
        return []

    tracks = _collect_caption_tracks(info or {}, preferred_langs)
    for track in tracks:
        parsed = _download_and_parse_caption_track(track)
        if parsed:
            return parsed
    return []


def _collect_caption_tracks(
    info: dict,
    preferred_langs: list[str],
) -> list[dict]:
    candidates: list[dict] = []
    sources = [("subtitles", 0), ("automatic_captions", 1)]
    ext_priority = {"json3": 0, "vtt": 1, "srv3": 2, "ttml": 3, "xml": 4}
    lang_order = {lang: idx for idx, lang in enumerate(preferred_langs)}

    for source_key, source_priority in sources:
        source = info.get(source_key) or {}
        if not isinstance(source, dict):
            continue
        for lang, items in source.items():
            if not isinstance(items, list):
                continue
            language_score = lang_order.get(str(lang), len(lang_order) + 1)
            for item in items:
                if not isinstance(item, dict):
                    continue
                track_url = str(item.get("url") or "").strip()
                ext = str(item.get("ext") or "").strip().lower()
                if not track_url or not ext:
                    continue
                candidates.append(
                    {
                        "url": track_url,
                        "ext": ext,
                        "language_score": language_score,
                        "source_priority": source_priority,
                        "ext_priority": ext_priority.get(ext, 99),
                    }
                )

    candidates.sort(
        key=lambda item: (
            item["language_score"],
            item["source_priority"],
            item["ext_priority"],
        )
    )
    return candidates


def _download_and_parse_caption_track(track: dict) -> list[TranscriptSegment]:
    url = str(track.get("url") or "").strip()
    ext = str(track.get("ext") or "").strip().lower()
    if not url:
        return []

    try:
        content = _download_text(url)
    except Exception:
        return []

    if ext == "json3":
        return _parse_json3_transcript(content)
    if ext == "vtt":
        return _parse_vtt_transcript(content)
    return []


def _download_text(url: str) -> str:
    request = Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        },
    )
    try:
        with urlopen(request, timeout=20) as response:
            raw = response.read()
    except URLError as exc:
        raise RuntimeError("Failed to download caption track.") from exc
    return raw.decode("utf-8", errors="ignore")


def _parse_json3_transcript(content: str) -> list[TranscriptSegment]:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return []

    events = payload.get("events")
    if not isinstance(events, list):
        return []

    segments: list[TranscriptSegment] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        parts = event.get("segs") or []
        if not isinstance(parts, list):
            continue
        text = "".join(str(part.get("utf8", "")) for part in parts if isinstance(part, dict))
        text = html.unescape(text).replace("\n", " ").strip()
        if not text:
            continue
        start_ms = float(event.get("tStartMs") or 0)
        duration_ms = float(event.get("dDurationMs") or 0)
        segments.append(
            TranscriptSegment(
                start=max(0.0, start_ms / 1000.0),
                duration=max(0.0, duration_ms / 1000.0),
                text=text,
            )
        )
    return segments


def _parse_vtt_transcript(content: str) -> list[TranscriptSegment]:
    segments: list[TranscriptSegment] = []
    blocks = re.split(r"\n\s*\n", content.replace("\r", ""))
    for block in blocks:
        lines = [line.strip() for line in block.split("\n") if line.strip()]
        if not lines:
            continue
        time_idx = 0 if "-->" in lines[0] else 1
        if time_idx >= len(lines) or "-->" not in lines[time_idx]:
            continue

        time_range = lines[time_idx]
        text_lines = lines[time_idx + 1 :]
        if not text_lines:
            continue

        try:
            start_raw, end_raw = [part.strip() for part in time_range.split("-->", 1)]
            start = _parse_vtt_time(start_raw)
            end = _parse_vtt_time(end_raw.split(" ")[0])
        except Exception:
            continue

        text = " ".join(text_lines)
        text = re.sub(r"<[^>]+>", "", text)
        text = html.unescape(text).replace("\n", " ").strip()
        if not text:
            continue

        segments.append(
            TranscriptSegment(
                start=max(0.0, start),
                duration=max(0.0, end - start),
                text=text,
            )
        )
    return segments


def _parse_vtt_time(value: str) -> float:
    normalized = value.replace(",", ".")
    parts = normalized.split(":")
    if len(parts) == 2:
        minute = int(parts[0])
        second = float(parts[1])
        return minute * 60 + second
    if len(parts) == 3:
        hour = int(parts[0])
        minute = int(parts[1])
        second = float(parts[2])
        return hour * 3600 + minute * 60 + second
    raise ValueError("Invalid VTT timestamp.")


def format_timestamp(seconds: float) -> str:
    value = max(0, int(round(seconds)))
    hour = value // 3600
    minute = (value % 3600) // 60
    second = value % 60
    return f"{hour:02d}:{minute:02d}:{second:02d}"


def parse_timestamp_to_seconds(timestamp: str | int | float) -> float:
    if isinstance(timestamp, (int, float)):
        return max(0.0, float(timestamp))

    raw = str(timestamp).strip()
    if not raw:
        return 0.0

    if raw.isdigit():
        return float(raw)

    parts = raw.split(":")
    if len(parts) == 2:
        minute, second = parts
        return float(int(minute) * 60 + int(second))
    if len(parts) == 3:
        hour, minute, second = parts
        return float(int(hour) * 3600 + int(minute) * 60 + int(second))

    match = re.match(r"^(\d+)(?:\.\d+)?$", raw)
    if match:
        return float(match.group(1))

    raise ValueError(f"Invalid timestamp: {timestamp}")


def transcript_to_timestamped_text(segments: list[TranscriptSegment]) -> str:
    return "\n".join(f"[{format_timestamp(item.start)}] {item.text}" for item in segments)


def ensure_binary_installed(binary_name: str) -> None:
    if shutil.which(binary_name) is None:
        raise RuntimeError(
            f"Required binary '{binary_name}' was not found. Please install it first."
        )


def download_video(youtube_url: str, output_dir: Path) -> tuple[Path, dict]:
    ensure_binary_installed("ffmpeg")
    output_dir.mkdir(parents=True, exist_ok=True)

    options = {
        "outtmpl": str(output_dir / "%(id)s.%(ext)s"),
        "format": "bv*+ba/b",
        "merge_output_format": "mp4",
        "quiet": True,
        "noprogress": True,
    }

    with YoutubeDL(options) as ydl:
        info = ydl.extract_info(youtube_url, download=True)

    video_id = info.get("id")
    if not video_id:
        raise RuntimeError("Unable to determine downloaded video id.")

    candidates = sorted(
        output_dir.glob(f"{video_id}.*"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise RuntimeError("Video download completed but output file was not found.")

    return candidates[0], info


def extract_frame(video_path: Path, timestamp_seconds: float, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-ss",
        f"{max(0.0, timestamp_seconds):.3f}",
        "-i",
        str(video_path),
        "-frames:v",
        "1",
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            "Failed to extract frame with ffmpeg. "
            f"stderr: {result.stderr.strip() or 'no stderr'}"
        )


def clamp_timestamp(seconds: float, video_duration_seconds: float | None) -> float:
    if video_duration_seconds is None or video_duration_seconds <= 0:
        return max(0.0, seconds)
    if seconds >= video_duration_seconds:
        return max(0.0, video_duration_seconds - 1)
    return max(0.0, seconds)
