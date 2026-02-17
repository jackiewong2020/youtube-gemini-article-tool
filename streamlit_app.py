from __future__ import annotations

import argparse
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import streamlit as st
from dotenv import load_dotenv

from history_store import append_history_record, load_history_records
from main import run_pipeline


DEFAULT_PROMPT = (
    "写成一篇面向公众号读者的深度文章，结构清晰，逻辑严谨，"
    "提炼可执行建议，语气自然口语化。"
)
STRATEGY_OPTIONS = {
    "视频截图优先（推荐）": "video_only",
    "混合：截图优先，失败后 AI 补图": "hybrid",
    "AI 补图（不截视频）": "ai_only",
}


load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env", override=False)


def _build_args(
    url: str,
    prompt: str,
    model: str,
    image_strategy: str,
    gemini_image_model: str,
    target_words: int,
    max_images: int,
    workspace: str,
    oss_prefix: str,
    oss_style: str,
    skip_upload: bool,
) -> argparse.Namespace:
    return argparse.Namespace(
        url=url,
        prompt=prompt,
        model=model,
        image_strategy=image_strategy,
        gemini_image_model=gemini_image_model,
        target_words=target_words,
        max_images=max_images,
        workspace=workspace,
        oss_prefix=oss_prefix,
        oss_style=oss_style,
        skip_upload=skip_upload,
    )


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
        "image_strategy": args.image_strategy,
        "gemini_image_model": args.gemini_image_model,
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


def _render_history(workspace: Path) -> None:
    st.subheader("历史记录")
    records = load_history_records(workspace, limit=50)
    if not records:
        st.info("暂无历史记录。")
        return

    display_rows = []
    for item in records:
        display_rows.append(
            {
                "时间": item.get("created_at", ""),
                "状态": item.get("status", ""),
                "视频": item.get("source_url", ""),
                "字数": item.get("target_words", ""),
                "图片数": item.get("image_count", ""),
                "配图策略": item.get("image_strategy", ""),
                "文章文件": item.get("article", ""),
            }
        )
    st.dataframe(display_rows, use_container_width=True)

    latest = records[0]
    if latest.get("status") == "success" and latest.get("article"):
        latest_article = Path(str(latest["article"]))
        if latest_article.exists():
            with st.expander("最近一次产出预览", expanded=False):
                st.markdown(latest_article.read_text(encoding="utf-8"))


def _check_access() -> bool:
    required_password = os.getenv("APP_PASSWORD", "").strip()
    if not required_password:
        return True

    if st.session_state.get("authenticated"):
        return True

    st.warning("此页面已启用访问密码。")
    password_input = st.text_input("访问密码", type="password")
    if st.button("登录访问", type="primary"):
        if password_input == required_password:
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.error("密码错误，请重试。")
    return False


def main() -> None:
    st.set_page_config(page_title="YouTube 图文自动化工具", layout="wide")
    if not _check_access():
        st.stop()

    st.title("YouTube -> Gemini 自动图文工具")
    st.caption("可视化进度、自动截帧、OSS 上传、Markdown 输出、历史留存")

    workspace_default = str((Path(__file__).resolve().parent / "workspace").resolve())

    with st.sidebar:
        st.header("参数配置")
        url = st.text_input("YouTube 链接", value="https://www.youtube.com/watch?v=N7NsveOiG-g")
        model = st.text_input("Gemini 模型", value="gemini-2.5-pro")
        strategy_label = st.selectbox(
            "配图策略",
            options=list(STRATEGY_OPTIONS.keys()),
            index=0,
        )
        gemini_image_model = st.text_input("Gemini 图片模型（AI补图用）", value="gemini-2.5-flash-image")
        target_words = st.number_input("目标字数", min_value=800, max_value=10000, value=3500, step=100)
        max_images = st.number_input(
            "最大配图数（0=自动）",
            min_value=0,
            max_value=20,
            value=0,
            step=1,
        )
        workspace = st.text_input("工作目录", value=workspace_default)
        oss_prefix = st.text_input("OSS 路径前缀", value="wechat_article")
        oss_style = st.text_input("OSS 图片样式", value="wechat-style")
        skip_upload = st.checkbox("跳过 OSS 上传（仅本地图片）", value=False)

    prompt = st.text_area(
        "文章提示词（可自定义）",
        value=DEFAULT_PROMPT,
        height=140,
    )

    col_run, col_history = st.columns([1, 1])
    run_clicked = col_run.button("开始生成", type="primary", use_container_width=True)
    show_history = col_history.button("刷新历史", use_container_width=True)

    workspace_path = Path(workspace).resolve()

    if run_clicked:
        args = _build_args(
            url=url.strip(),
            prompt=prompt.strip(),
            model=model.strip(),
            image_strategy=STRATEGY_OPTIONS[strategy_label],
            gemini_image_model=gemini_image_model.strip(),
            target_words=int(target_words),
            max_images=int(max_images),
            workspace=str(workspace_path),
            oss_prefix=oss_prefix.strip(),
            oss_style=oss_style.strip(),
            skip_upload=skip_upload,
        )

        progress = st.progress(0.0, text="准备开始...")
        logs_box = st.empty()
        logs: list[str] = []

        def on_progress(stage: str, detail: str, value: float) -> None:
            progress.progress(value, text=f"{stage}（{int(value * 100)}%）")
            logs.append(f"- `{datetime.now().strftime('%H:%M:%S')}` {stage}：{detail}")
            logs_box.markdown("\n".join(logs[-12:]))

        try:
            result = run_pipeline(args, progress_callback=on_progress)
            _record_history(workspace_path, args, status="success", result=result)
        except Exception as exc:
            _record_history(workspace_path, args, status="failed", error=str(exc))
            st.error(f"执行失败：{exc}")
        else:
            st.success("生成完成。")
            st.write(f"文章：`{result['article']}`")
            st.write(f"Manifest：`{result['manifest']}`")
            st.write(f"配图数量：`{result['image_count']}`")
            st.write(f"配图策略：`{STRATEGY_OPTIONS[strategy_label]}`")

            article_path = Path(str(result["article"]))
            if article_path.exists():
                with st.expander("查看文章结果", expanded=True):
                    st.markdown(article_path.read_text(encoding="utf-8"))

    if show_history or not run_clicked:
        _render_history(workspace_path)


if __name__ == "__main__":
    main()
