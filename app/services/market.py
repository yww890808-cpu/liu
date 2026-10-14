"""选品：抓取第三方市场数据并分析。

- 每天按“关注的类目”抓取前一天销售额前 N 的商品，存为每日快照；
- 新增类目时回补最近 MARKET_BACKFILL_DAYS 天；
- 某天数据为空视为数据服务尚未出数，停止并在下次重试，不会跳过这一天。
"""
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import MarketCategory, MarketCategoryDaily, MarketProduct, MarketProductDaily
from app.market.mpstats import MPStatsClient

logger = logging.getLogger(__name__)


# ---------------- 字段解析（兼容数据服务的字段命名） ----------------

def _pick(item: dict, *keys):
    for k in keys:
        v = item.get(k)
        if v not in (None, ""):
            return v
    return None


def _int(v) -> int | None:
    try:
        return int(float(v)) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _dec(v) -> Decimal | None:
    try:
        return Decimal(str(v)) if v not in (None, "") else None
    except InvalidOperation:
        return None


def _day(v) -> date | None:
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v)[:10]).date()
    except ValueError:
        return None


# ---------------- 类目管理 ----------------

class MarketError(ValueError):
    pass


def add_category(db: Session, path: str, name: str | None = None, top_n: int = 500) -> MarketCategory:
    path = path.strip().strip("/")
    if not path:
        raise MarketError("类目路径不能为空")
    if db.scalar(select(MarketCategory).where(MarketCategory.path == path)):
        raise MarketError("该类目已在关注列表中")
    cat = MarketCategory(path=path, name=name or path.rsplit("/", 1)[-1], top_n=top_n)
    db.add(cat)
    db.commit()
    return cat


# ---------------- 抓取 ----------------

@dataclass
class FetchResult:
    days: int = 0
    products: int = 0
    pending: bool = False  # 数据服务尚未出数

    def __str__(self) -> str:
        s = f"抓取 {self.days} 天，共 {self.products} 条商品数据"
        return s + ("；最近一天数据服务尚未出数，下次重试" if self.pending else "")


def save_day(db: Session, cat: MarketCategory, day: date, items: list[dict]) -> int:
    """写入某个类目某天的商品数据，同一商品同一天重复写入时覆盖。"""
    skus = [s for s in (_int(_pick(i, "id", "sku")) for i in items) if s]
    products = {p.ozon_sku: p for p in db.scalars(select(MarketProduct).where(MarketProduct.ozon_sku.in_(skus)))}
    count = 0
    for item in items:
        sku = _int(_pick(item, "id", "sku"))
        if not sku:
            continue
        p = products.get(sku)
        if p is None:
            p = products[sku] = MarketProduct(ozon_sku=sku, first_seen=day, last_seen=day)
            db.add(p)
            db.flush()
        p.name = _pick(item, "name") or p.name
        p.brand = _pick(item, "brand") or p.brand
        p.seller = _pick(item, "seller", "seller_name") or p.seller
        p.thumb = _pick(item, "thumb_middle", "thumb", "image") or p.thumb
        p.category_id = cat.id
        p.first_seen, p.last_seen = min(p.first_seen, day), max(p.last_seen, day)

        row = db.scalar(select(MarketProductDaily).where(
            MarketProductDaily.product_id == p.id, MarketProductDaily.day == day))
        if row is None:
            row = MarketProductDaily(product_id=p.id, day=day)
            db.add(row)
        row.price = _dec(_pick(item, "final_price", "price"))
        row.sales = _int(_pick(item, "sales")) or 0
        row.revenue = _dec(_pick(item, "revenue")) or Decimal(0)
        row.stock = _int(_pick(item, "balance", "stock"))
        row.rating = _dec(_pick(item, "rating"))
        row.reviews = _int(_pick(item, "comments", "reviews"))
        count += 1
    return count


def save_category_days(db: Session, cat: MarketCategory, rows: list[dict]) -> int:
    count = 0
    for r in rows:
        day = _day(_pick(r, "period", "date", "day"))
        if day is None:
            continue
        row = db.scalar(select(MarketCategoryDaily).where(
            MarketCategoryDaily.category_id == cat.id, MarketCategoryDaily.day == day))
        if row is None:
            row = MarketCategoryDaily(category_id=cat.id, day=day)
            db.add(row)
        row.sales = _int(_pick(r, "sales"))
        row.revenue = _dec(_pick(r, "revenue"))
        row.items = _int(_pick(r, "items"))
        row.items_with_sales = _int(_pick(r, "items_with_sells", "items_with_sales"))
        row.sellers = _int(_pick(r, "sellers"))
        row.avg_price = _dec(_pick(r, "avg_price", "average_price"))
        count += 1
    return count


def days_to_fetch(cat: MarketCategory, today: date) -> list[date]:
    yesterday = today - timedelta(days=1)
    start = yesterday - timedelta(days=get_settings().market_backfill_days - 1)
    if cat.last_fetched_date:
        start = max(start, cat.last_fetched_date + timedelta(days=1))
    return [start + timedelta(days=i) for i in range((yesterday - start).days + 1)]


def fetch_category(db: Session, client: MPStatsClient, cat: MarketCategory, today: date) -> FetchResult:
    result = FetchResult()
    days = days_to_fetch(cat, today)
    for day in days:
        items = client.category_items(cat.path, day, day, cat.top_n)
        if not items:
            result.pending = True
            break
        result.products += save_day(db, cat, day, items)
        cat.last_fetched_date = day
        result.days += 1
        db.commit()  # 每天提交一次，中途失败时已完成的天不丢
    if result.days:
        # 类目每日汇总是辅助数据，失败不影响商品数据
        try:
            save_category_days(db, cat, client.category_by_date(cat.path, days[0], days[result.days - 1]))
            db.commit()
        except Exception as e:
            db.rollback()
            logger.warning("类目每日汇总获取失败 %s：%s", cat.path, e)
    return result


# ---------------- 分析 ----------------

SORT_FIELDS = {"revenue_30d", "sales_30d", "sales_7d", "growth_pct", "price", "rating",
               "reviews", "reviews_growth", "days_with_sales", "first_seen"}


def latest_day(db: Session) -> date | None:
    return db.scalar(select(func.max(MarketProductDaily.day)))


def ranking(db: Session, *, category_id: int | None = None, q: str | None = None,
            price_min: float | None = None, price_max: float | None = None,
            min_sales_30d: int | None = None, min_growth: float | None = None,
            favorites: bool = False, sort: str = "revenue_30d", desc: bool = True,
            page: int = 1, size: int = 50) -> dict:
    """按最近 30 天表现给商品排名。时间窗口以已有数据的最新日期为准。"""
    as_of = latest_day(db)
    if as_of is None:
        return {"as_of": None, "total": 0, "items": []}
    d7, d14, d30 = (as_of - timedelta(days=n - 1) for n in (7, 14, 30))
    D = MarketProductDaily

    agg = (
        select(
            D.product_id.label("pid"),
            func.sum(case((D.day >= d7, D.sales), else_=0)).label("sales_7d"),
            func.sum(case((and_(D.day >= d14, D.day < d7), D.sales), else_=0)).label("sales_prev_7d"),
            func.sum(D.sales).label("sales_30d"),
            func.sum(D.revenue).label("revenue_30d"),
            func.sum(case((D.sales > 0, 1), else_=0)).label("days_with_sales"),
            func.min(D.reviews).label("reviews_min"),
            func.max(D.day).label("last_day"),
        )
        .where(D.day >= d30, D.day <= as_of)
        .group_by(D.product_id)
        .subquery()
    )
    latest = D.__table__.alias("latest")
    growth = case((agg.c.sales_prev_7d > 0,
                   (agg.c.sales_7d - agg.c.sales_prev_7d) * 100.0 / agg.c.sales_prev_7d), else_=None)
    reviews_growth = latest.c.reviews - agg.c.reviews_min
    columns = {
        "sales_7d": agg.c.sales_7d, "sales_30d": agg.c.sales_30d, "revenue_30d": agg.c.revenue_30d,
        "growth_pct": growth, "price": latest.c.price, "rating": latest.c.rating,
        "reviews": latest.c.reviews, "reviews_growth": reviews_growth,
        "days_with_sales": agg.c.days_with_sales, "first_seen": MarketProduct.first_seen,
    }
    stmt = (
        select(MarketProduct, MarketCategory.name.label("category_name"), agg.c.sales_7d, agg.c.sales_prev_7d,
               agg.c.sales_30d, agg.c.revenue_30d, agg.c.days_with_sales, growth.label("growth_pct"),
               reviews_growth.label("reviews_growth"), latest.c.price, latest.c.rating, latest.c.reviews,
               latest.c.stock)
        .join(agg, agg.c.pid == MarketProduct.id)
        .join(latest, and_(latest.c.product_id == agg.c.pid, latest.c.day == agg.c.last_day))
        .outerjoin(MarketCategory, MarketCategory.id == MarketProduct.category_id)
    )
    if category_id:
        stmt = stmt.where(MarketProduct.category_id == category_id)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(MarketProduct.name.ilike(like), MarketProduct.brand.ilike(like),
                              MarketProduct.seller.ilike(like)))
    if price_min is not None:
        stmt = stmt.where(latest.c.price >= price_min)
    if price_max is not None:
        stmt = stmt.where(latest.c.price <= price_max)
    if min_sales_30d:
        stmt = stmt.where(agg.c.sales_30d >= min_sales_30d)
    if min_growth is not None:
        stmt = stmt.where(growth >= min_growth)
    if favorites:
        stmt = stmt.where(MarketProduct.is_favorite.is_(True))

    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    col = columns[sort if sort in SORT_FIELDS else "revenue_30d"]
    order = (col.desc() if desc else col.asc()).nulls_last()
    rows = db.execute(stmt.order_by(order, MarketProduct.id).offset((page - 1) * size).limit(size)).all()
    items = []
    for r in rows:
        p = r.MarketProduct
        items.append({
            "id": p.id, "ozon_sku": p.ozon_sku, "name": p.name, "brand": p.brand, "seller": p.seller,
            "thumb": p.thumb, "category_name": r.category_name, "is_favorite": p.is_favorite, "note": p.note,
            "first_seen": p.first_seen, "price": r.price, "rating": r.rating, "reviews": r.reviews,
            "stock": r.stock, "sales_7d": r.sales_7d, "sales_prev_7d": r.sales_prev_7d,
            "sales_30d": r.sales_30d, "revenue_30d": r.revenue_30d, "days_with_sales": r.days_with_sales,
            "growth_pct": round(float(r.growth_pct), 1) if r.growth_pct is not None else None,
            "reviews_growth": r.reviews_growth,
        })
    return {"as_of": as_of, "total": total, "items": items}


def product_history(db: Session, product_id: int, days: int = 60) -> list[MarketProductDaily]:
    as_of = latest_day(db) or date.today()
    return list(db.scalars(select(MarketProductDaily).where(
        MarketProductDaily.product_id == product_id,
        MarketProductDaily.day > as_of - timedelta(days=days)).order_by(MarketProductDaily.day)))


def category_history(db: Session, category_id: int, days: int = 60) -> list[dict]:
    """类目每日走势：数据服务给出的全类目汇总 + 本系统跟踪的前 N 商品合计。"""
    as_of = latest_day(db) or date.today()
    since = as_of - timedelta(days=days)
    D = MarketProductDaily
    tracked = {r.day: r for r in db.execute(
        select(D.day, func.sum(D.sales).label("sales"), func.sum(D.revenue).label("revenue"),
               func.count().label("products"))
        .join(MarketProduct, MarketProduct.id == D.product_id)
        .where(MarketProduct.category_id == category_id, D.day > since)
        .group_by(D.day))}
    totals = {r.day: r for r in db.scalars(select(MarketCategoryDaily).where(
        MarketCategoryDaily.category_id == category_id, MarketCategoryDaily.day > since))}
    out = []
    for day in sorted(set(tracked) | set(totals)):
        t, c = tracked.get(day), totals.get(day)
        out.append({
            "day": day,
            "tracked_sales": t.sales if t else None, "tracked_revenue": t.revenue if t else None,
            "tracked_products": t.products if t else None,
            "sales": c.sales if c else None, "revenue": c.revenue if c else None,
            "items": c.items if c else None, "items_with_sales": c.items_with_sales if c else None,
            "sellers": c.sellers if c else None, "avg_price": c.avg_price if c else None,
        })
    return out
