# Ozon 电商自动化平台（MVP）

内部使用的 Ozon FBS 店铺自动化后台：订单拉取、发货处理、库存同步。需求见 MVP 需求文档。

> 当前进度：阶段 1（项目骨架）、阶段 2（Ozon API 封装层）已完成。接口清单见 `docs/ozon-endpoints.md`（待确认）。

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
```

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

## 数据库迁移

```bash
alembic revision --autogenerate -m "说明"   # 修改 app/db/models.py 后生成迁移
alembic upgrade head
```

## 安全约定

- 真实 `.env` 已加入 `.gitignore`，不要提交；只提交 `.env.example`。
- 店铺 Api-Key 以 Fernet 密文存库，加密密钥 `FERNET_KEY` 只放环境变量。
- `DRY_RUN=true`（默认）时，备货 / 改库存 / 回传单号等会改动线上数据的操作只记录不提交。
