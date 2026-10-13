# Ozon 电商自动化平台（MVP）

内部使用的 Ozon FBS 店铺自动化后台：订单拉取、发货处理、库存同步。需求见 MVP 需求文档。

> 当前进度：阶段 1–5 已完成（骨架、Ozon API 封装、店铺管理 + 商品/仓库同步、订单拉取、发货）。接口清单见 `docs/ozon-endpoints.md`（待确认）。

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
frontend/          Vue 3 管理后台（阶段 7）
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

# 创建后台登录账号（交互输入密码）
docker compose exec api python -m app.cli create-user admin
```

接口文档（可直接在浏览器里试用）：http://localhost:8000/docs ，先点右上角 Authorize 登录。

会启动 5 个服务：`postgres`、`redis`、`api`（启动前自动执行 `alembic upgrade head`）、`worker`（Celery）、`beat`（Celery 定时调度）。
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

需要 Celery 时本地另起 Redis，然后：

```bash
celery -A app.tasks.celery_app worker -l info
celery -A app.tasks.celery_app beat -l info
```

## 已有接口

| 接口 | 说明 |
|---|---|
| `POST /api/auth/login` | 登录，返回 JWT |
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

定时任务：
- 每 `ORDER_SYNC_INTERVAL_MINUTES` 分钟拉取订单：最近 `ORDER_SYNC_LOOKBACK_DAYS` 天内状态有变化、且创建于最近 `ORDER_SYNC_CREATED_WINDOW_DAYS` 天内的货件；按货件号去重。
- 每天 `PRODUCT_SYNC_HOUR` 点（UTC）同步所有启用店铺的仓库与商品。
所有任务结果写入 `task_logs`，所有 Ozon 调用写入 `api_logs`。

## 发货流程（使用 Ozon 物流）

1. 备货确认：校验订单为“待备货”、各包裹商品数量合计与订单一致后提交。拆包后 Ozon 会为每个包裹生成新货件号，系统自动拉取并分别记录。
2. 面单：备货成功后等待 `LABEL_FETCH_DELAY_SEC` 秒自动获取并保存到 `LABEL_DIR`；Ozon 返回“未就绪”时按同样间隔重试，最多 `LABEL_FETCH_MAX_TRIES` 次。
3. 物流单号由 Ozon 生成，随订单同步显示，无需回传。
4. 需要报关单号或商品标识码的订单不自动备货，提示到 Ozon 后台处理。

**`DRY_RUN=true`（默认）时备货只在 `task_logs`（`ship_dry_run`）中记录将要提交的内容，不调用 Ozon。**

## 路线图

| 阶段 | 内容 | 状态 |
|---|---|---|
| 1–5 | 骨架、API 封装、店铺/商品、订单、发货 | 已完成 |
| 6 | 库存同步（按仓库） | 待开发 |
| 7 | 管理后台前端 | 待开发 |
| 8 | 整体测试、部署文档 | 待开发 |
| 9 | 选品：接入第三方 Ozon 数据服务（如 MPStats、Moneyplace），每日查看全平台商品销量 | MVP 完成后开发，需先选定服务商并提供 API 文档与密钥 |

## 数据库迁移

```bash
alembic revision --autogenerate -m "说明"   # 修改 app/db/models.py 后生成迁移
alembic upgrade head
```

## 安全约定

- 真实 `.env` 已加入 `.gitignore`，不要提交；只提交 `.env.example`。
- 店铺 Api-Key 以 Fernet 密文存库，加密密钥 `FERNET_KEY` 只放环境变量。
- `DRY_RUN=true`（默认）时，备货 / 改库存 / 回传单号等会改动线上数据的操作只记录不提交。
