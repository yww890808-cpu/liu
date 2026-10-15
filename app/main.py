"""FastAPI 入口。"""
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api import auth, logs, market, orders, shops, stock
from app.config import check_secrets, get_settings

check_secrets()
app = FastAPI(title="Ozon 自动化平台", version="0.1.0")
app.include_router(auth.router)
app.include_router(shops.router)
app.include_router(orders.router)
app.include_router(stock.router)
app.include_router(logs.router)
app.include_router(market.router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "dry_run": get_settings().dry_run}


# 管理后台前端（frontend 构建产物）。存在时由后端直接提供，前后端同一端口。
_static = Path(get_settings().static_dir).resolve()
if (_static / "index.html").exists():
    app.mount("/assets", StaticFiles(directory=_static / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith(("api/", "docs", "openapi.json", "redoc")):
            raise HTTPException(404)
        file = _static / path
        if path and file.is_file() and _static in file.resolve().parents:
            return FileResponse(file)
        return FileResponse(_static / "index.html")  # 前端路由交给 index.html
