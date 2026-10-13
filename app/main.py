"""FastAPI 入口。"""
from fastapi import FastAPI

from app.config import get_settings

app = FastAPI(title="Ozon 自动化平台", version="0.1.0")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "dry_run": get_settings().dry_run}
