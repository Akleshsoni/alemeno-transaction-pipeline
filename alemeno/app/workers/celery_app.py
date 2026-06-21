from celery import Celery
from app.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "transactions",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=["app.workers.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_routes={"app.workers.tasks.*": {"queue": "transactions"}},
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_track_started=True,
)
