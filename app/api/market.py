"""选品接口：关注类目、商品排行、商品与类目走势。"""
import time
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import current_user
from app.config import get_settings
from app.db.models import MarketCategory, MarketProduct
from app.db.session import SessionLocal, get_db
from app.market.mpstats import MPStatsClient, MPStatsError, MPStatsNotConfigured
from app.services import market as market_service
from app.tasks.market import fetch_market_category_task

router = APIRouter(prefix="/api/market", tags=["选品"], dependencies=[Depends(current_user)])

_catalog_cache: dict = {"at": 0.0, "items": []}
_CATALOG_TTL = 24 * 3600


def _client() -> MPStatsClient:
    try:
        return MPStatsClient(db_factory=SessionLocal)
    except MPStatsNotConfigured as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e


class CategoryIn(BaseModel):
    path: str = Field(min_length=1, max_length=512)
    name: str | None = None
    top_n: int = Field(default=500, ge=10, le=5000)


class CategoryPatch(BaseModel):
    top_n: int | None = Field(default=None, ge=10, le=5000)
    is_active: bool | None = None


class CategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    path: str
    name: str
    top_n: int
    is_active: bool
    last_fetched_date: date | None
    products: int = 0


class ProductPatch(BaseModel):
    is_favorite: bool | None = None
    note: str | None = None


@router.get("/status")
def market_status() -> dict:
    """数据服务配置情况与剩余调用额度。"""
    if not get_settings().mpstats_token:
        return {"configured": False}
    client = _client()
    try:
        return {"configured": True, "limit": client.api_limit()}
    except MPStatsError as e:
        return {"configured": True, "error": str(e)}
    finally:
        client.close()


@router.get("/catalog")
def catalog(q: str = Query(min_length=1, description="类目名称关键词（俄语）")) -> list[dict]:
    """在 Ozon 类目树中搜索（结果缓存 1 天）。"""
    if time.time() - _catalog_cache["at"] > _CATALOG_TTL or not _catalog_cache["items"]:
        client = _client()
        try:
            _catalog_cache.update(items=client.categories(), at=time.time())
        except MPStatsError as e:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e)) from e
        finally:
            client.close()
    ql = q.lower()
    return [c for c in _catalog_cache["items"] if ql in (c.get("path") or "").lower()][:50]


@router.get("/categories", response_model=list[CategoryOut])
def list_categories(db: Session = Depends(get_db)):
    counts = dict(db.execute(select(MarketProduct.category_id, func.count()).group_by(MarketProduct.category_id)).all())
    cats = db.scalars(select(MarketCategory).order_by(MarketCategory.id)).all()
    return [CategoryOut.model_validate(c).model_copy(update={"products": counts.get(c.id, 0)}) for c in cats]


@router.post("/categories", response_model=CategoryOut, status_code=status.HTTP_201_CREATED)
def add_category(body: CategoryIn, db: Session = Depends(get_db)):
    try:
        cat = market_service.add_category(db, body.path, body.name, body.top_n)
    except market_service.MarketError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    if get_settings().mpstats_token:
        fetch_market_category_task.delay(cat.id)  # 立即回补最近几天
    return cat


@router.patch("/categories/{category_id}", response_model=CategoryOut)
def update_category(category_id: int, body: CategoryPatch, db: Session = Depends(get_db)):
    cat = db.get(MarketCategory, category_id) or _404()
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(cat, k, v)
    db.commit()
    return cat


@router.delete("/categories/{category_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_category(category_id: int, db: Session = Depends(get_db)):
    """取消关注：保留已抓取的商品与历史数据。"""
    db.delete(db.get(MarketCategory, category_id) or _404())
    db.commit()


@router.post("/categories/{category_id}/fetch", status_code=status.HTTP_202_ACCEPTED)
def fetch_now(category_id: int, db: Session = Depends(get_db)) -> dict:
    db.get(MarketCategory, category_id) or _404()
    _client().close()  # 未配置 token 时直接提示
    return {"task_id": fetch_market_category_task.delay(category_id).id}


@router.get("/categories/{category_id}/history")
def category_history(category_id: int, days: int = Query(default=60, ge=7, le=365),
                     db: Session = Depends(get_db)) -> list[dict]:
    db.get(MarketCategory, category_id) or _404()
    return market_service.category_history(db, category_id, days)


@router.get("/products")
def products(db: Session = Depends(get_db), category_id: int | None = None, q: str | None = None,
             price_min: float | None = None, price_max: float | None = None,
             min_sales_30d: int | None = None, min_growth: float | None = None,
             favorites: bool = False, sort: str = "revenue_30d", desc: bool = True,
             page: int = Query(default=1, ge=1), size: int = Query(default=50, ge=1, le=200)) -> dict:
    return market_service.ranking(db, category_id=category_id, q=q, price_min=price_min, price_max=price_max,
                                  min_sales_30d=min_sales_30d, min_growth=min_growth, favorites=favorites,
                                  sort=sort, desc=desc, page=page, size=size)


@router.get("/products/{product_id}")
def product_detail(product_id: int, days: int = Query(default=60, ge=7, le=365),
                   db: Session = Depends(get_db)) -> dict:
    p = db.get(MarketProduct, product_id) or _404()
    history = market_service.product_history(db, p.id, days)
    return {
        "id": p.id, "ozon_sku": p.ozon_sku, "name": p.name, "brand": p.brand, "seller": p.seller,
        "thumb": p.thumb, "is_favorite": p.is_favorite, "note": p.note,
        "first_seen": p.first_seen, "last_seen": p.last_seen,
        "url": f"https://www.ozon.ru/product/{p.ozon_sku}/",
        "history": [{"day": h.day, "sales": h.sales, "revenue": h.revenue, "price": h.price,
                     "stock": h.stock, "rating": h.rating, "reviews": h.reviews} for h in history],
    }


@router.patch("/products/{product_id}")
def update_product(product_id: int, body: ProductPatch, db: Session = Depends(get_db)) -> dict:
    p = db.get(MarketProduct, product_id) or _404()
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(p, k, v)
    db.commit()
    return {"id": p.id, "is_favorite": p.is_favorite, "note": p.note}


def _404():
    raise HTTPException(status.HTTP_404_NOT_FOUND, "不存在")
