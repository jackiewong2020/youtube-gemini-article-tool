from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from history_store import append_history_record

from llm_writer import generate_article_plan
from oss_uploader import OSSUploader, preprocess_image_for_wechat
from video_pipeline import (
    clamp_timestamp,
    download_video,
    extract_frame,
    extract_video_id,
    fetch_transcript,
    parse_timestamp_to_seconds,
    transcript_to_timestamped_text,
)


def _insert_image_markdown(body_markdown: str, image_markdown: str, anchor: str) -> str:
    content = body_markdown.strip()
    anchor_value = anchor.strip()

    if anchor_value and anchor_value in content:
        return content.replace(
            anchor_value,
            f"{anchor_value}\n\n{image_markdown}\n\n",
            1,
        )

    if not content:
        return image_markdown

    return f"{content}\n\n{image_markdown}\n"


def _render_markdown(plan: dict[str, Any]) -> str:
    lines: list[str] = [f"# {plan['title']}"]

    if plan.get("lead"):
        lines.extend(["", plan["lead"]])

    for section in plan["sections"]:
        lines.extend(["", f"## {section['heading']}"])
        lines.append(section["body_markdown"])

    if plan.get("conclusion"):
        lines.extend(["", "## 总结", plan["conclusion"]])

    if plan.get("tags"):
        lines.extend(["", "关键词：" + " / ".join(plan["tags"])])

    lines.append("")
    return "\n".join(lines)


def _apply_oss_style(image_url: str, style_name: str) -> str:
    style = style_name.strip()
    if not style:
        return image_url

    if style.startswith("!"):
        style = style[1:]
    if style.startswith("x-oss-process=style/"):
        style = style.split("x-oss-process=style/", 1)[1]
    if style.startswith("style/"):
        style = style.split("style/", 1)[1]
    if not style:
        return image_url

    separator = "&" if "?" in image_url else "?"
    return f"{image_url}{separator}x-oss-process=style/{style}"


def _notify_progress(
    callback: Callable[[str, str, float], None] | None,
    stage: str,
    detail: str,
    progress: float,
) -> None:
    if not callback:
        return
    callback(stage, detail, max(0.0, min(1.0, progress)))


def _record_history(
    workspace: Path,
    args: argparse.Namespace,
    status: str,
    result: dict[str, Any] | None = None,
    error: str = "",
) -> None:
    payload = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "status": status,
        "source_url": args.url,
        "model": args.model,
        "target_words": args.target_words,
        "max_images": args.max_images,
        "skip_upload": bool(args.skip_upload),
        "oss_prefix": args.oss_prefix,
        "oss_style": args.oss_style,
        "error": error,
    }
    if result:
        payload.update(result)
    append_history_record(workspace, payload)


def _build_paths(workspace: Path) -> dict[str, Path]:
    paths = {
        "root": workspace,
        "video": workspace / "video",
        "transcript": workspace / "transcript",
        "plan": workspace / "plan",
        "frames_raw": workspace / "frames" / "raw",
        "frames_wechat": workspace / "frames" / "wechat",
        "output": workspace / "output",
        "history": workspace / "history",
    }
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
    return paths


def run_pipeline(
    args: argparse.Namespace,
    progress_callback: Callable[[str, str, float], None] | None = None,
) -> dict[str, Any]:
    workspace = Path(args.workspace).resolve()
    paths = _build_paths(workspace)

    _notify_progress(progress_callback, "初始化", "解析视频链接", 0.03)
    video_id = extract_video_id(args.url)

    _notify_progress(progress_callback, "提取字幕", "正在抓取 YouTube 字幕", 0.10)
    transcript = fetch_transcript(video_id)
    transcript_text = transcript_to_timestamped_text(transcript)
    transcript_path = paths["transcript"] / f"{video_id}.txt"
    transcript_path.write_text(transcript_text, encoding="utf-8")

    max_images = args.max_images if args.max_images > 0 else None
    _notify_progress(progress_callback, "生成文案", "调用 Gemini 生成文章与配图计划", 0.22)
    article_plan = generate_article_plan(
        transcript_text=transcript_text,
        user_instruction=args.prompt,
        model_name=args.model,
        target_words=args.target_words,
        max_images=max_images,
    )

    plan_path = paths["plan"] / f"{video_id}.json"
    plan_path.write_text(
        json.dumps(article_plan, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    _notify_progress(progress_callback, "下载视频", "正在下载并准备截帧", 0.34)
    video_path, video_info = download_video(args.url, paths["video"])
    duration = float(video_info.get("duration") or 0)

    uploader = None
    if not args.skip_upload:
        _notify_progress(progress_callback, "初始化图床", "加载 OSS 配置", 0.40)
        try:
            uploader = OSSUploader.from_env()
        except Exception as exc:
            print(f"[WARN] OSS not configured, fallback to local image paths. reason={exc}")

    image_manifest: list[dict[str, Any]] = []
    date_prefix = datetime.now().strftime("%Y%m/%d")
    style_suffix = args.oss_style.strip()
    images_to_process = sum(
        1 for section in article_plan["sections"] if section.get("image", {}).get("need")
    )
    handled_images = 0

    for idx, section in enumerate(article_plan["sections"], start=1):
        image = section.get("image", {})
        if not image.get("need"):
            continue

        handled_images += 1
        base = 0.42
        span = 0.48
        step_progress = base
        if images_to_process > 0:
            step_progress += span * ((handled_images - 1) / images_to_process)
        _notify_progress(
            progress_callback,
            "处理配图",
            f"第 {handled_images}/{images_to_process} 张：{section['heading']}",
            step_progress,
        )

        timestamp_seconds = parse_timestamp_to_seconds(image.get("timestamp", "00:00:00"))
        timestamp_seconds = clamp_timestamp(timestamp_seconds, duration)

        raw_path = paths["frames_raw"] / f"{video_id}_{idx:02d}.png"
        optimized_path = paths["frames_wechat"] / f"{video_id}_{idx:02d}.webp"

        extract_frame(video_path, timestamp_seconds, raw_path)
        preprocess_image_for_wechat(raw_path, optimized_path)

        image_url: str
        if uploader:
            oss_key = (
                f"{args.oss_prefix.strip('/')}/{date_prefix}/{video_id}/{optimized_path.name}"
            )
            image_url = uploader.upload_file(optimized_path, oss_key)
            image_url = _apply_oss_style(image_url, style_suffix)
        else:
            image_url = optimized_path.as_uri()

        alt = image.get("alt") or image.get("caption") or section["heading"]
        image_markdown = f"![{alt}]({image_url})"
        section["body_markdown"] = _insert_image_markdown(
            section["body_markdown"],
            image_markdown,
            anchor=image.get("anchor", ""),
        )

        image_manifest.append(
            {
                "section_index": idx,
                "heading": section["heading"],
                "timestamp": image.get("timestamp", "00:00:00"),
                "seconds": timestamp_seconds,
                "local_image": str(optimized_path),
                "image_url": image_url,
                "caption": image.get("caption", ""),
            }
        )

    _notify_progress(progress_callback, "生成文件", "写入 Markdown 与 manifest", 0.94)
    markdown = _render_markdown(article_plan).replace("\ufffd", "")
    article_basename = f"{video_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    markdown_path = paths["output"] / f"{article_basename}.md"
    manifest_path = paths["output"] / f"{article_basename}.manifest.json"

    markdown_path.write_text(markdown, encoding="utf-8")
    manifest_path.write_text(
        json.dumps(
            {
                "source_url": args.url,
                "video_id": video_id,
                "video_path": str(video_path),
                "transcript_path": str(transcript_path),
                "plan_path": str(plan_path),
                "article_path": str(markdown_path),
                "generated_at": datetime.now().isoformat(timespec="seconds"),
                "images": image_manifest,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    _notify_progress(progress_callback, "完成", "全部流程执行成功", 1.0)
    return {
        "article": str(markdown_path),
        "manifest": str(manifest_path),
        "plan": str(plan_path),
        "transcript": str(transcript_path),
        "image_count": len(image_manifest),
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate article + screenshots from a YouTube URL using Gemini and OSS."
    )
    parser.add_argument("--url", required=True, help="YouTube video URL")
    parser.add_argument("--prompt", required=True, help="Article writing instruction")
    parser.add_argument("--model", default="gemini-2.5-pro", help="Gemini model name")
    parser.add_argument(
        "--target-words",
        type=int,
        default=3500,
        help="Target word count for article",
    )
    parser.add_argument(
        "--max-images",
        type=int,
        default=0,
        help="Max screenshots to insert; 0 means auto by content",
    )
    parser.add_argument(
        "--workspace",
        default="./workspace",
        help="Working directory for generated files",
    )
    parser.add_argument(
        "--oss-prefix",
        default="wechat_article",
        help="OSS object key prefix",
    )
    parser.add_argument(
        "--oss-style",
        default="",
        help="Optional OSS style suffix (for example: wechat-style)",
    )
    parser.add_argument(
        "--skip-upload",
        action="store_true",
        help="Skip OSS upload and keep local image URLs",
    )
    return parser


def main() -> None:
    parser = build_arg_parser()
    args = parser.parse_args()
    workspace = Path(args.workspace).resolve()

    try:
        result = run_pipeline(args)
    except Exception as exc:
        _record_history(
            workspace=workspace,
            args=args,
            status="failed",
            error=str(exc),
        )
        print(f"[ERROR] {exc}")
        raise SystemExit(1) from exc

    _record_history(
        workspace=workspace,
        args=args,
        status="success",
        result=result,
    )

    print("Done.")
    print(f"Article: {result['article']}")
    print(f"Manifest: {result['manifest']}")
    print(f"Plan: {result['plan']}")
    print(f"Transcript: {result['transcript']}")
    print(f"Images inserted: {result['image_count']}")


if __name__ == "__main__":
    main()
