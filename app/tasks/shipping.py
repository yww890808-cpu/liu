"""面单获取任务：备货后延迟获取，未就绪时按间隔重试。"""
from app.config import get_settings
from app.db.models import Order, Shop
from app.db.session import SessionLocal
from app.ozon.errors import OzonAPIError
from app.services import shipping
from app.services.shops import ozon_client_for
from app.services.task_log import run_logged
from app.tasks.celery_app import celery_app


def schedule_label_fetch(order_id: int) -> None:
    fetch_label_task.apply_async((order_id,), countdown=get_settings().label_fetch_delay_sec)


@celery_app.task(name="shipping.fetch_label", bind=True)
def fetch_label_task(self, order_id: int) -> str | None:
    settings = get_settings()
    with SessionLocal() as db:
        order = db.get(Order, order_id)
        if order is None:
            return None
        shop_id = order.shop_id

    def job() -> str:
        with SessionLocal() as db:
            order = db.get(Order, order_id)
            client = ozon_client_for(db.get(Shop, order.shop_id))
            try:
                return str(shipping.fetch_and_store_label(db, client, order))
            finally:
                client.close()

    try:
        return run_logged("fetch_label", shop_id, job)
    except OzonAPIError as e:
        if shipping.is_label_not_ready(e) and self.request.retries + 1 < settings.label_fetch_max_tries:
            raise self.retry(countdown=settings.label_fetch_delay_sec) from e
        raise
