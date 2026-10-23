# 上线部署指南（单机全托管方案）

> 适用栈：FastAPI + SQLite + APScheduler（进程内定时任务）+ React/Vite 静态前端。
> 不建议拆 Serverless（SQLite 文件库与多进程定时任务在 serverless 下都会出问题）。
> 预计耗时：首次约 30 分钟。

## 0. 你需要准备

- 一台 Linux 服务器（Ubuntu 22.04）：云学生机约 10 元/月，或实验室/校园网服务器
- 一个域名（可选；校内部署用 IP 即可）
- 仓库的 git 权限 + 生产环境的 LLM API Key

## 1. 服务器初始化

```bash
sudo apt update && sudo apt install -y python3.12-venv nginx git
sudo mkdir -p /var/www/mapaopao && sudo chown $USER:$USER /var/www/mapaopao
```

## 2. 拉代码 + 建环境

```bash
cd /var/www/mapaopao
git clone https://github.com/SamuelPsq/mapaopao.git .
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 生产配置（绝不进 git）：LLM Key、数据库路径、CORS 白名单
cat > .env <<'EOF'
DASHSCOPE_API_KEY=sk-你的生产key
MAPAOPAO_DB_URL=sqlite:////var/www/mapaopao/data/mapaopao.db
MAPAOPAO_CORS_ORIGINS=https://你的域名
EOF
mkdir -p data
```

## 3. 构建前端

```bash
cd app && npm install && npm run build   # 产物 app/dist，Nginx 直接服务
```

## 4. 后端常驻（systemd）

```bash
sudo cp deploy/mapaopao.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now mapaopao
systemctl status mapaopao        # 应显示 active (running)
curl http://127.0.0.1:8000/docs  # 后端自检
```

APScheduler 随进程启动自动注册全部定时任务（周快照/天气刷新/周问卷/调参建议），无需 crontab。

## 5. Nginx 反代

```bash
sudo cp deploy/nginx.conf /etc/nginx/sites-available/mapaopao
sudo ln -sf /etc/nginx/sites-available/mapaopao /etc/nginx/sites-enabled/
# 编辑 server_name 为你的域名/IP 后：
sudo nginx -t && sudo systemctl reload nginx
```

浏览器打开 `http://你的域名` → 注册页出现《数据使用协议》勾选即全链路通了。

## 6. 上线前 Checklist

- [ ] `MAPAOPAO_DEV` **不设置**——`/dev/shutdown`、`/dev/restart` 只在 dev 模式注册
- [ ] `.env`、`mapaopao.db` 在 `.gitignore` 内（仓库已配好），服务器上 `chmod 600 .env`
- [ ] `/v1/lab/*` 已用 teacher 账号验证过 403 拦截（普通账号不可达）
- [ ] `pytest tests/` 全量跑过（4 个日期敏感的存量失败可忽略）
- [ ] **数据备份**：研究数据值钱，每日备份 SQLite：
      `0 3 * * * cp /var/www/mapaopao/data/mapaopao.db /backup/mapaopao-$(date +\%F).db`

## 7. HTTPS（对外服务建议做）

```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d 你的域名    # 自动改 Nginx 配置并续期
```

## 备选：零成本校内演示

实验室 Windows 机器跑 `start.bat`，把 uvicorn host 改 `0.0.0.0`，用校园内网 IP 直接访问；
需外网时加 frp 穿透到一台有公网 IP 的跳板机即可。

## 常见坑

| 现象 | 原因 |
| --- | --- |
| 前端能开但所有请求 404 | Nginx 没配 `/v1/` 反代（前端用相对路径，必须同源） |
| 定时任务不跑 | uvicorn 用了 `--workers >1`：APScheduler 是进程内的，**保持单进程** |
| 上传照片 413 | nginx `client_max_body_size` 默认 1m，配置里已加到 10m |
| 重启后数据没了 | `MAPAOPAO_DB_URL` 用了相对路径 `sqlite:///./mapaopao.db`，重启进程工作目录变了——用绝对路径 |
