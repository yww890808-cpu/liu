# Ozon 电商自动化平台（MVP）

内部使用的 Ozon FBS 店铺自动化后台：订单拉取、发货处理、库存同步。需求见 MVP 需求文档。

> MVP（阶段 1–8）与选品（阶段 9）已完成。
> - **用 Codex 一键安装 / 更新**：[docs/codex-install.md](docs/codex-install.md)（复制整段发给 Codex 即可）
> - **部署**：[docs/deploy.md](docs/deploy.md)（自己电脑 / 云服务器 HTTPS、更新、备份恢复、常见问题）
> - **首次接入真实店铺**：[docs/go-live-checklist.md](docs/go-live-checklist.md)
> - Ozon 接口清单：[docs/ozon-endpoints.md](docs/ozon-endpoints.md)

## 一键安装（用 Codex）

先安装并打开 [Docker Desktop](https://www.docker.com/products/docker-desktop/)，然后把下面整段复制发给电脑上的 Codex（代码框右上角有复制按钮）。准备事项、安装后步骤、更新方法见 [docs/codex-install.md](docs/codex-install.md)。

```text
请帮我在这台电脑上安装并启动一个项目，按顺序做，每一步告诉我结果，遇到报错先停下来告诉我原因，不要自己乱改。

1. 运行 docker info 检查 Docker 是否在运行。没有运行就停下来提醒我打开 Docker Desktop。
2. 在当前文件夹下载代码并进入：
   git clone https://github.com/yww890808-cpu/liu.git
   cd liu
   如果 liu 文件夹已经存在，改为进入 liu 后执行 git pull，然后直接跳到第 5 步。
3. 把 .env.example 复制一份，命名为 .env。
4. 运行下面这条命令两次，得到两串随机字符：
   docker run --rm python:3.11-slim python -c "import secrets,base64;print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"
   第一串填到 .env 的 FERNET_KEY= 后面，第二串填到 JWT_SECRET= 后面。
   其他配置不要改，DRY_RUN 必须保持 true。
5. 启动：docker compose up -d --build（第一次需要几分钟）
6. 等 30 秒后检查：
   - docker compose ps 里 6 个服务（postgres、redis、api、worker、beat、backup）都在运行
   - 访问 http://localhost:8000/health 返回 "status":"ok"
   有服务没起来就运行 docker compose logs api 把报错告诉我。
7. 完成后提醒我：把 .env 文件另外备份一份到安全的地方，里面的 FERNET_KEY 丢了会导致已保存的店铺密钥无法解密。

注意：
- 不要修改项目里的任何代码文件
- 不要在回复里显示 .env 中的密钥内容
- 不要执行 git commit、git push
```

装好后：浏览器打开 http://localhost:8000 直接进入后台（默认不需要登录）。

## 目录结构

```
app/
  main.py          FastAPI 入口（/health）
  config.py        配置（pydantic-settings，读取 .env）
  db/              数据库引擎、会话、模型
  ozon/            Ozon API 封装：client.py（限流/重试/日志）、errors.py、ratelimit.py
  services/        业务逻辑（阶段 3–6）
  api/             后台接口路由
  tasks/           Celery 应用与定时任务
  utils/           加密、日志等工具
frontend/          Vue 3 + Element Plus 管理后台（构建后由后端在同一端口提供）
migrations/        Alembic 迁移
tests/             单元测试
```

## 一键启动（Docker）

```bash
cp .env.example .env
# 编辑 .env：至少填写 FERNET_KEY 和 JWT_SECRET
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"   # 生成 FERNET_KEY

docker compose up -d --build
curl http://localhost:8000/health      # {"status":"ok","dry_run":true}
```

**管理后台：浏览器打开 http://localhost:8000 直接进入（默认不需要登录）。**

> 免登录意味着能打开这个网址的人都能操作店铺。只在自己电脑上用没问题；同一 Wi-Fi 下的其他设备也能访问。
> 需要登录时在 `.env` 设置 `LOGIN_REQUIRED=true`，并用 `docker compose exec api python -m app.cli create-user admin` 创建账号。云服务器部署默认强制登录。

- 订单：按店铺/状态/时间筛选，查看详情，备货（支持拆包）、批量备货，下载/批量打印面单
- 库存：每个商品在各仓库的库存一屏查看，直接修改后保存，手动推送或从 Ozon 读取
- 店铺：添加/编辑/删除店铺，测试连接，同步商品，立即拉取订单，开关仓库库存推送
- 选品：关注 Ozon 类目，每天自动抓取全平台商品销量，按销量/销售额/周增长/评论增长排行，查看商品与类目走势，标记候选商品
- 日志：任务执行记录、Ozon 接口调用记录（可只看失败）

同一 Wi-Fi 下的手机可以打开 `http://电脑的局域网IP:8000` 使用（订单页在手机上为卡片布局）。

接口文档（开发调试用）：http://localhost:8000/docs

会启动 6 个服务：`postgres`、`redis`、`api`（启动前自动执行 `alembic upgrade head`）、`worker`（Celery）、`beat`（Celery 定时调度）、`backup`（每天备份数据库到 `backups/`）。
云服务器部署（HTTPS 域名）见 [docs/deploy.md](docs/deploy.md)。
compose 中会覆盖 `DATABASE_URL` / `REDIS_URL` 指向容器内服务，`.env` 里的这两项只用于本地开发。

## 本地开发（不用 Docker）

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env          # 默认使用 SQLite: ./dev.db
alembic upgrade head
uvicorn app.main:app --reload
pytest
```

前端开发（热更新，接口自动转发到 8000 端口）：

```bash
cd frontend && npm install && npm run dev     # 打开 http://localhost:5173
npm run build                                  # 构建到 frontend/dist，后端启动时自动提供
```

需要 Celery 时本地另起 Redis，然后：

```bash
celery -A app.tasks.celery_app worker -l info
celery -A app.tasks.celery_app beat -l info
```

## 已有接口

| 接口 | 说明 |
|---|---|
| `POST /api/auth/login` | 登录，返回 JWT（仅 `LOGIN_REQUIRED=true` 时需要） |
| `GET/POST /api/shops` | 店铺列表 / 添加店铺（Api-Key 加密存储，返回时脱敏） |
| `PATCH/DELETE /api/shops/{id}` | 修改（名称、Api-Key、启用状态）/ 删除店铺 |
| `POST /api/shops/{id}/test` | 测试店铺凭证是否可用（只读接口） |
| `POST /api/shops/{id}/sync` | 后台同步该店铺的仓库与商品列表 |
| `GET /api/shops/{id}/warehouses` | 仓库列表；`PATCH .../warehouses/{wid}` 启用/停用某仓库的库存推送 |
| `GET /api/shops/{id}/products` | 商品列表（分页、按货号/名称搜索） |
| `POST /api/shops/{id}/sync-orders` | 立即拉取该店铺订单 |
| `GET /api/orders` | 订单列表：按店铺、状态（可多选）、仓库、创建时间、货件号/货号/商品名筛选 |
| `GET /api/orders/statuses` | 各状态订单数 |
| `GET /api/orders/{id}` | 订单详情（含商品明细和 Ozon 原始数据） |
| `POST /api/orders/{id}/refresh` | 从 Ozon 重新获取该订单 |
| `POST /api/orders/{id}/ship` | 备货确认；不传 `packages` 为整单一包，拆包时传每个包裹的 `[{sku, quantity}]` |
| `POST /api/orders/ship-batch` | 批量备货（每单一包），逐单返回结果 |
| `GET /api/orders/{id}/label` | 下载面单 PDF（本地没有时立即向 Ozon 获取） |
| `POST /api/orders/labels` | 批量面单：同一店铺最多 20 单，合并为一个 PDF |
| `GET /api/shops/{id}/stocks` | 库存列表：每个商品在各仓库的本地库存、已推送值、Ozon 当前值、是否待推送 |
| `PUT /api/shops/{id}/stocks` | 批量修改本地库存 `[{product_id, warehouse_id, local_stock}]` |
| `POST /api/shops/{id}/stocks/push` | 立即推送待同步库存 |
| `POST /api/shops/{id}/stocks/refresh` | 从 Ozon 读取当前库存（只读） |
| `GET /api/logs/tasks`、`GET /api/logs/api` | 任务执行记录、Ozon 接口调用记录 |

定时任务：
- 每 `ORDER_SYNC_INTERVAL_MINUTES` 分钟拉取订单：最近 `ORDER_SYNC_LOOKBACK_DAYS` 天内状态有变化、且创建于最近 `ORDER_SYNC_CREATED_WINDOW_DAYS` 天内的货件；按货件号去重。
- 每 `STOCK_PUSH_INTERVAL_MINUTES` 分钟推送库存：只推本地值与上次推送值不同的记录。
- 每天 `PRODUCT_SYNC_HOUR` 点（UTC）同步所有启用店铺的仓库、商品，并读取 Ozon 当前库存。
所有任务结果写入 `task_logs`，所有 Ozon 调用写入 `api_logs`。

## 发货流程（使用 Ozon 物流）

1. 备货确认：校验订单为“待备货”、各包裹商品数量合计与订单一致后提交。拆包后 Ozon 会为每个包裹生成新货件号，系统自动拉取并分别记录。
2. 面单：备货成功后等待 `LABEL_FETCH_DELAY_SEC` 秒自动获取并保存到 `LABEL_DIR`；Ozon 返回“未就绪”时按同样间隔重试，最多 `LABEL_FETCH_MAX_TRIES` 次。
3. 物流单号由 Ozon 生成，随订单同步显示，无需回传。
4. 需要报关单号或商品标识码的订单不自动备货，提示到 Ozon 后台处理。

**`DRY_RUN=true`（默认）时备货只在 `task_logs`（`ship_dry_run`）中记录将要提交的内容，不调用 Ozon。**

## 选品（第三方数据：MPStats）

Ozon 官方的全平台数据接口（Statistics API）不对卖家开放，因此选品数据来自第三方服务 MPStats（销量、销售额为其估算值）。

1. 在 https://mpstats.io 开通包含 API 的订阅，在个人中心“API token”处复制 token。
2. 填入 `.env` 的 `MPSTATS_TOKEN=`，执行 `docker compose up -d`。
3. 管理后台“选品 → 关注类目 → 添加类目”：输入俄语关键词搜索类目（如 `кружки`），设置每天抓取销售额前 N 的商品。
4. 添加后自动回补最近 `MARKET_BACKFILL_DAYS` 天（默认 14）；之后每天 `MARKET_FETCH_HOUR` 点（UTC，默认 5 点 = 莫斯科 8 点）抓取前一天的数据。数据服务尚未出数的日期会在下次自动重试。
5. “商品排行”中可按 7/30 天销量、30 天销售额、周增长（近 7 天对比前 7 天）、评论增长、价格等排序筛选；点击商品查看每日销量、价格、评论走势；★ 标记候选并写备注。

调用量：每个类目每天约 2 次调用（商品列表 + 类目汇总），回补时每天 1 次。剩余额度显示在选品页顶部。

| 接口 | 说明 |
|---|---|
| `GET /api/market/status` | 数据服务配置情况与剩余调用额度 |
| `GET /api/market/catalog?q=` | 搜索 Ozon 类目 |
| `GET/POST /api/market/categories` | 关注类目列表 / 添加；`PATCH`、`DELETE`、`POST .../fetch`、`GET .../history` |
| `GET /api/market/products` | 商品排行（筛选、排序、分页） |
| `GET/PATCH /api/market/products/{id}` | 商品详情与每日走势 / 标记候选、备注 |

## 库存规则

- 库存按“商品 × 仓库”维护，填写的是**可售数量**（不含已被订单预留的部分），Ozon 会自行扣减新订单。
- 首次读取 Ozon 库存时，用 Ozon 当前可售数（总数 − 预留）初始化本地库存；之后读取只更新展示值，不覆盖本地修改。
- **从未设置过本地库存的记录永不推送**，避免误把 Ozon 库存清零。
- 已归档商品、停用的仓库不推送；推送失败的记录保留原因（`push_error`），下一轮自动重推。
- Ozon 限制：同一商品同一仓库每 2 分钟只能更新一次，每次最多 100 条。
- `DRY_RUN=true` 时只在 `task_logs`（`stock_push_dry_run`）记录将要推送的内容。

## 路线图

| 阶段 | 内容 | 状态 |
|---|---|---|
| 1–7 | 骨架、API 封装、店铺/商品、订单、发货、库存同步、管理后台 | 已完成 |
| 8 | 整体测试、部署文档、自动备份、安全加固 | 已完成 |
| 9 | 选品：接入 MPStats，每日抓取关注类目的全平台商品销量并分析 | 已完成（需配置 `MPSTATS_TOKEN`） |

## 数据库迁移

```bash
alembic revision --autogenerate -m "说明"   # 修改 app/db/models.py 后生成迁移
alembic upgrade head
```

## 安全约定

- 真实 `.env` 已加入 `.gitignore`，不要提交；只提交 `.env.example`。
- 店铺 Api-Key 以 Fernet 密文存库，加密密钥 `FERNET_KEY` 只放环境变量。
- `DRY_RUN=true`（默认）时，备货、库存推送等会改动线上数据的操作只记录不提交。
- 启动时校验 `FERNET_KEY` / `JWT_SECRET`，缺失或格式错误直接报错退出。
- 默认免登录（`LOGIN_REQUIRED=false`），适合只在自己电脑上使用；云服务器部署（`docker-compose.prod.yml`）默认强制登录。
- 开启登录时，同一用户名或 IP 连续输错密码 5 次，锁定 15 分钟。
- 云服务器部署时只开放 80/443（Caddy 自动 HTTPS），后端端口仅本机可访问。
