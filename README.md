# YouTube -> Gemini 自动图文工具（可视化版）

输入 YouTube 链接后，自动完成：
- 提取字幕与时间戳
- 无字幕时自动走 Gemini 音频转写兜底
- 生成约 3500 字文章（默认）
- 根据内容需要自动决定配图位置
- 支持配图策略：视频截图优先 / 混合补图 / AI 补图
- 上传 OSS 并生成中国可访问图片链接
- 保存历史记录，支持回看每次产出

## 1. 环境要求

- Python 3.10+
- `ffmpeg`
- Gemini API Key
- 阿里云 OSS（可选，不配置也可本地跑通）

安装 ffmpeg（macOS）：

```bash
brew install ffmpeg
```

## 2. 安装依赖

```bash
cd /Users/jackie/Movies/Youtubepost
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 3. 配置环境变量

```bash
cp .env.example .env
```

`.env` 至少需要：
- `GEMINI_API_KEY`
- `GEMINI_IMAGE_MODEL`（可选，默认 `gemini-2.5-flash-image`）
- `GEMINI_TRANSCRIBE_MODEL`（可选，默认 `gemini-2.5-flash`）
- `GEMINI_TRANSCRIBE_FALLBACK`（可选，默认 `true`）

如果启用 OSS 上传，再填写：
- `OSS_ACCESS_KEY_ID`
- `OSS_ACCESS_KEY_SECRET`
- `OSS_ENDPOINT`
- `OSS_BUCKET_NAME`
- `OSS_DOMAIN`
- `OSS_STYLE`（可选，例：`wechat-style`）

可选安全项：
- `APP_PASSWORD`（给可视化页面加访问密码）

## 4. 启动可视化工具（推荐）

```bash
cd /Users/jackie/Movies/Youtubepost
./.venv/bin/streamlit run streamlit_app.py
```

打开后可配置：
- 视频链接
- 自定义提示词
- 配图策略（截图优先/混合/AI）
- 目标字数（默认 3500）
- 最大配图数（`0=自动按内容决定`）
- OSS 参数与是否跳过上传

界面会显示实时进度和历史记录。

## 5. 命令行模式

```bash
cd /Users/jackie/Movies/Youtubepost
./.venv/bin/python main.py \
  --url "https://www.youtube.com/watch?v=N7NsveOiG-g" \
  --prompt "写成一篇面向公众号读者的深度文章，结构清晰，提炼可执行建议，语气自然口语化" \
  --image-strategy "video_only" \
  --target-words 3500 \
  --max-images 0 \
  --oss-prefix "wechat_article" \
  --oss-style "wechat-style"
```

说明：
- `--target-words` 默认 `3500`
- `--max-images 0` 表示不固定张数，由内容决定
- `--image-strategy` 可选：
  - `video_only`：只用视频截图（默认）
  - `hybrid`：截图失败自动切换 AI 补图
  - `ai_only`：全部使用 AI 补图

## 6. 输出目录

默认在 `workspace/`：
- `workspace/transcript/*.txt`：字幕
- `workspace/plan/*.json`：文章计划（含配图时间点）
- `workspace/frames/raw/*.png`：原始截图/AI 原图
- `workspace/frames/wechat/*.webp`：优化后图片
- `workspace/output/*.md`：最终文章
- `workspace/output/*.manifest.json`：图文映射
- `workspace/history/runs.jsonl`：历史记录

## 7. 部署到 GitHub

如果当前目录还不是 Git 仓库：

```bash
cd /Users/jackie/Movies/Youtubepost
git init
git add .
git commit -m "feat: add visual app, progress tracking, and history"
```

创建远程仓库后推送：

```bash
git branch -M main
git remote add origin <你的仓库地址>
git push -u origin main
```

> 注意：`.env` 已被 `.gitignore` 忽略，不会上传密钥。

## 8. 部署到 Hostinger VPS

本项目依赖 Python + `ffmpeg`，建议使用 Hostinger VPS（不是普通共享虚拟主机）。

快速启动：

```bash
cd /Users/jackie/Movies/Youtubepost
docker compose up -d --build
```

完整步骤见：
- `/Users/jackie/Movies/Youtubepost/deploy/hostinger-vps.md`
