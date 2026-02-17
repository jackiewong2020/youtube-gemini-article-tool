from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import parse_qs, urlparse

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
) -> list[TranscriptSegment]:
    preferred_langs = list(languages or ("zh-Hans", "zh-CN", "zh", "en"))

    try:
        api = YouTubeTranscriptApi()
        fetched = api.fetch(video_id, languages=preferred_langs)
        raw_segments = fetched.to_raw_data()
    except Exception as exc:
        raise RuntimeError(
            "Failed to fetch transcript. Ensure subtitles are available for this video."
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
