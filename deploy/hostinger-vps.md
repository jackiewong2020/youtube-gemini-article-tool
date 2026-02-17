# Hostinger VPS 部署指南

本工具包含 Python + `ffmpeg` + `yt-dlp`，建议部署在 **Hostinger VPS**，不要放在普通共享虚拟主机。

## 1) VPS 登录并安装基础环境

```bash
ssh root@<你的VPS公网IP>
apt update && apt install -y git docker.io docker-compose-plugin
systemctl enable docker --now
```

## 2) 拉取项目并准备环境变量

```bash
git clone https://github.com/jackiewong2020/youtube-gemini-article-tool.git
cd youtube-gemini-article-tool
cp .env.example .env
nano .env
```

至少填写：

- `GEMINI_API_KEY`
- `OSS_ACCESS_KEY_ID`
- `OSS_ACCESS_KEY_SECRET`
- `OSS_ENDPOINT`
- `OSS_BUCKET_NAME`
- `OSS_DOMAIN`

建议填写：

- `APP_PASSWORD`（保护可视化页面）
- `OSS_STYLE=wechat-style`

## 3) 启动服务

```bash
docker compose up -d --build
docker compose ps
```

看到 `Up` 后，访问：

```text
http://<你的VPS公网IP>:8501
```

## 4) 可选：绑定域名 + HTTPS（推荐）

你可以在 Hostinger DNS 创建 `A` 记录到 VPS IP，然后用 Nginx/Caddy 反代 8501 并签发证书。

## 5) 常用运维命令

```bash
cd ~/youtube-gemini-article-tool
git pull
docker compose up -d --build
docker compose logs -f
docker compose down
```
