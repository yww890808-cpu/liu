"""FastAPI 入口。"""
from fastapi import FastAPI

from app.api import auth, orders, shops, stock
from app.config import get_settings

app = FastAPI(title="Ozon 自动化平台", version="0.1.0")
app.include_router(auth.router)
app.include_router(shops.router)
app.include_router(orders.router)
app.include_router(stock.router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "dry_run": get_settings().dry_run}
