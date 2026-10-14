# 部署说明

两种方式任选其一：

| 方式 | 适合 | 手机访问 |
|---|---|---|
| A. 自己的电脑 | 先试用、验证真实店铺 | 只能在同一 Wi-Fi 下 |
| B. 云服务器 | 正式使用，24 小时自动拉单、推库存 | 随时随地（HTTPS 域名） |

> 系统需要一直开着才能定时拉单、推送库存。电脑关机或休眠期间不会执行，开机后自动恢复。

---

## A. 自己的电脑（Windows / Mac）

### 1. 安装 Docker Desktop
https://www.docker.com/products/docker-desktop/ ，安装后打开，等它显示 Running。

### 2. 下载代码
```bash
git clone -b claude/ozon-mvp-phase1 https://github.com/yww890808-cpu/liu.git
cd liu
```
没有 git 时，可在 GitHub 网页切换到该分支后 Code → Download ZIP 并解压。

### 3. 生成配置
```bash
# Windows：copy .env.example .env      Mac：cp .env.example .env
docker run --rm python:3.11-slim python -c "import secrets,base64;print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"
```
上面的命令运行两次，用记事本打开 `.env`，第一次的结果填入 `FERNET_KEY=`，第二次填入 `JWT_SECRET=`。

> **`FERNET_KEY` 务必另外保存一份**（比如记在密码管理器里）。丢失后已保存的店铺 Api-Key 无法解密，只能重新录入。

### 4. 启动并创建账号
```bash
docker compose up -d --build
docker compose exec api python -m app.cli create-user admin
```

### 5. 打开
浏览器访问 http://localhost:8000 。

同一 Wi-Fi 下的手机：先查电脑的局域网 IP（Windows 运行 `ipconfig` 看“IPv4 地址”；Mac 在“系统设置 → 网络”中查看），手机浏览器打开 `http://这个IP:8000`。打不开时检查电脑防火墙是否允许 8000 端口。

---

## B. 云服务器（推荐正式使用）

### 准备
- 一台 Linux 云服务器：Ubuntu 22.04/24.04，**2 核 4G** 内存即可。
- 一个域名，添加一条 A 记录指向服务器公网 IP，例如 `ozon.你的域名.com`。
- 服务器安全组 / 防火墙放行 **80 和 443** 端口（不要放行 8000）。

> 服务器在**中国大陆**时，域名需要先完成 ICP 备案才能使用 80/443 端口；香港或海外服务器不需要。服务器需要能访问 `api-seller.ozon.ru`。

### 1. 安装 Docker
```bash
curl -fsSL https://get.docker.com | sh
```

### 2. 下载代码并配置
```bash
git clone -b claude/ozon-mvp-phase1 https://github.com/yww890808-cpu/liu.git
cd liu
cp .env.example .env
python3 -c "import secrets,base64;print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"   # 运行两次
nano .env
```
在 `.env` 中填写 `FERNET_KEY`、`JWT_SECRET`，并加一行 `DOMAIN=ozon.你的域名.com`。

### 3. 启动
```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
docker compose exec api python -m app.cli create-user admin
```
Caddy 会自动申请 HTTPS 证书（需要域名已解析、80/443 已放行）。之后在任何地方用浏览器打开 `https://ozon.你的域名.com`。

生产配置的区别：
- 只有 Caddy 对外开放 80/443，后端 8000 端口只允许服务器本机访问；
- 登录连续输错 5 次密码会被锁定 15 分钟；
- 每天自动备份数据库。

> 需要 Docker Compose 2.24 或更高版本（`docker compose version` 查看）。上面的安装方式会装最新版。

---

## 日常维护

以下命令在 `liu` 目录下执行。云服务器上每条 `docker compose` 后面都要加 `-f docker-compose.yml -f docker-compose.prod.yml`（或者 `export COMPOSE_FILE=docker-compose.yml:docker-compose.prod.yml` 一次设置好）。

### 更新到最新版本
```bash
git pull
docker compose up -d --build
```
数据库结构变化会在启动时自动迁移，数据不会丢失。

### 查看运行状态和日志
```bash
docker compose ps                  # 所有服务应为 Up
docker compose logs -f worker      # 定时任务的运行日志
```
业务层面的记录（任务结果、Ozon 调用失败原因）直接在管理后台的“日志”页查看。

### 备份
- **自动备份**：每天一次，文件在 `backups/` 目录（`ozon-日期-时间.sql.gz`），保留 `BACKUP_KEEP_DAYS` 天（默认 14）。
- **手动备份**（升级前建议做一次）：
  ```bash
  docker compose exec backup sh -c 'pg_dump -h postgres -U ozon ozon | gzip > /backups/manual-$(date +%Y%m%d).sql.gz'
  ```
- 建议定期把 `backups/` 目录和 `.env` 文件复制到别的地方（网盘、另一台电脑）。服务器整体损坏时，有这两样就能完整恢复。

### 从备份恢复
```bash
docker compose stop api worker beat
docker compose exec backup sh -c "psql -h postgres -U ozon -d postgres -c 'DROP DATABASE ozon' -c 'CREATE DATABASE ozon'"
docker compose exec backup sh -c "gunzip -c /backups/要恢复的文件名.sql.gz | psql -h postgres -U ozon -d ozon -q"
docker compose start api worker beat
```
恢复需要使用**备份时的同一个 `FERNET_KEY`**，否则店铺 Api-Key 无法解密。

### 修改配置
编辑 `.env` 后执行 `docker compose up -d` 使配置生效。

### 增加登录账号 / 忘记密码
```bash
docker compose exec api python -m app.cli create-user 新用户名
```
忘记密码时可以新建一个账号使用。

---

## 常见问题

| 现象 | 处理 |
|---|---|
| 启动后 api 一直重启，日志里有“配置错误：FERNET_KEY …” | `.env` 中密钥没填或格式不对，按第 3 步重新生成填写 |
| `port is already allocated` / 端口被占用 | 8000（或 80/443）被其他程序占用，关闭该程序后重试 |
| 打开网页显示无法访问 | `docker compose ps` 确认服务都在运行；云服务器检查域名解析和安全组 80/443 |
| HTTPS 证书申请失败 | `docker compose logs caddy` 查看原因，通常是域名未解析到本机或 80 端口未放行 |
| 测试连接失败 | 在“日志 → Ozon 接口调用”查看具体错误：401/403 多为 Client-Id 或 Api-Key 错误，网络错误说明服务器访问不了 Ozon |
| 登录提示“密码错误次数过多” | 等 15 分钟，或重启 api：`docker compose restart api` |
