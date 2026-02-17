from __future__ import annotations

import argparse
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

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
IMAGE_MARKDOWN_PATTERN = re.compile(r"!\[(?P<alt>[^\]]*)\]\((?P<url>[^)]+)\)")
READING_WIDTH_PRESETS = {
    "紧凑（72%）": 72,
    "标准（82%）": 82,
    "宽屏（92%）": 92,
    "全宽（100%）": 100,
}


load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env", override=False)


def _apply_layout_style(
    reading_width_percent: int,
    enable_sidebar_resize: bool,
) -> None:
    sidebar_css = "resize: horizontal; overflow: auto;" if enable_sidebar_resize else ""
    st.markdown(
        f"""
<style>
    [data-testid="stMainBlockContainer"] {{
        max-width: {reading_width_percent}%;
        margin-left: auto;
        margin-right: auto;
    }}
    [data-testid="stSidebar"] {{
        min-width: 240px;
        max-width: 720px;
        {sidebar_css}
    }}
</style>
""",
        unsafe_allow_html=True,
    )


def _resolve_local_image_path(image_ref: str, workspace: Path) -> Path | None:
    value = image_ref.strip().strip('"').strip("'")
    if value.startswith("<") and value.endswith(">"):
        value = value[1:-1].strip()
    if " " in value:
        value = value.split(" ", 1)[0].strip()
    if not value:
        return None

    if value.startswith("file://"):
        parsed = urlparse(value)
        path_value = unquote(parsed.path or "")
        if os.name == "nt" and len(path_value) > 2 and path_value[0] == "/":
            path_value = path_value[1:]
        candidate = Path(path_value)
        return candidate if candidate.exists() else None

    if value.startswith("http://") or value.startswith("https://"):
        parsed = urlparse(value)
        remote_name = Path(unquote(parsed.path or "")).name
        return _find_local_image_by_name(workspace, remote_name)

    candidate = Path(value)
    if candidate.is_absolute() and candidate.exists():
        return candidate

    workspace_candidate = (workspace / value).resolve()
    if workspace_candidate.exists():
        return workspace_candidate

    project_candidate = (Path(__file__).resolve().parent / value).resolve()
    if project_candidate.exists():
        return project_candidate

    return None


def _find_local_image_by_name(workspace: Path, filename: str) -> Path | None:
    name = filename.strip()
    if not name:
        return None

    likely_dirs = [
        workspace / "frames" / "wechat",
        workspace / "frames" / "raw",
        workspace / "output",
    ]
    for folder in likely_dirs:
        candidate = folder / name
        if candidate.exists():
            return candidate

    try:
        for candidate in workspace.rglob(name):
            if candidate.is_file():
                return candidate
    except Exception:
        return None
    return None


def _render_article_preview(markdown_text: str, workspace: Path) -> None:
    text = str(markdown_text or "").strip()
    if not text:
        st.info("暂无文章内容。")
        return

    if not IMAGE_MARKDOWN_PATTERN.search(text):
        st.markdown(text)
        return

    cursor = 0
    for match in IMAGE_MARKDOWN_PATTERN.finditer(text):
        prefix = text[cursor : match.start()].strip()
        if prefix:
            st.markdown(prefix)

        alt = match.group("alt").strip() or "文章配图"
        image_ref = match.group("url").strip()
        local_image = _resolve_local_image_path(image_ref, workspace)

        if local_image:
            st.image(str(local_image), caption=alt, width="stretch")
        else:
            try:
                st.image(image_ref, caption=alt, width="stretch")
            except Exception:
                st.markdown(match.group(0))
        cursor = match.end()

    suffix = text[cursor:].strip()
    if suffix:
        st.markdown(suffix)


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

    def display_value(value: Any) -> str:
        if value is None:
            return ""
        return str(value)

    display_rows = []
    for item in records:
        display_rows.append(
            {
                "时间": display_value(item.get("created_at", "")),
                "状态": display_value(item.get("status", "")),
                "视频": display_value(item.get("source_url", "")),
                "字数": display_value(item.get("target_words", "")),
                "图片数": display_value(item.get("image_count", "")),
                "配图策略": display_value(item.get("image_strategy", "")),
                "文章文件": display_value(item.get("article", "")),
            }
        )
    st.dataframe(display_rows, width="stretch")

    latest = records[0]
    if latest.get("status") == "success" and latest.get("article"):
        latest_article = Path(str(latest["article"]))
        if latest_article.exists():
            with st.expander("最近一次产出预览", expanded=False):
                _render_article_preview(
                    latest_article.read_text(encoding="utf-8"),
                    workspace=workspace,
                )


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
        st.markdown("---")
        width_mode = st.selectbox(
            "阅读宽度预设",
            options=["标准（82%）", "紧凑（72%）", "宽屏（92%）", "全宽（100%）", "自定义"],
        )
        reading_width_percent = READING_WIDTH_PRESETS.get(width_mode, 82)
        if width_mode == "自定义":
            reading_width_percent = st.slider(
                "阅读区宽度（%）",
                min_value=65,
                max_value=100,
                value=82,
                step=1,
            )
        else:
            st.caption(f"当前阅读区宽度：{reading_width_percent}%")
        enable_sidebar_resize = st.checkbox("允许侧栏拖动宽度", value=True)

    _apply_layout_style(
        reading_width_percent=reading_width_percent,
        enable_sidebar_resize=enable_sidebar_resize,
    )

    prompt = st.text_area(
        "文章提示词（可自定义）",
        value=DEFAULT_PROMPT,
        height=140,
    )

    col_run, col_history = st.columns([1, 1])
    run_clicked = col_run.button("开始生成", type="primary", width="stretch")
    show_history = col_history.button("刷新历史", width="stretch")

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
                    _render_article_preview(
                        article_path.read_text(encoding="utf-8"),
                        workspace=workspace_path,
                    )

    if show_history or not run_clicked:
        _render_history(workspace_path)


if __name__ == "__main__":
    main()
