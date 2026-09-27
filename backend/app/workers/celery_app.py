import yaml
from celery import Celery
from celery.schedules import crontab

from app.core.settings import get_settings

settings = get_settings()

celery_app = Celery(
    "chargegrid",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.workers.tasks.ingestion", "app.workers.tasks.planning"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Kolkata",
    enable_utc=True,
)

# Section 13 cadences. Only jobs whose prerequisites are configured are
# scheduled, so a missing key never produces a nightly failure.
beat_schedule = {
    "osm-chargers-daily": {"task": "ingest.osm_chargers", "schedule": crontab(hour=2, minute=0)},
}
if settings.ocm_api_key:
    beat_schedule["ocm-daily"] = {"task": "ingest.ocm", "schedule": crontab(hour=2, minute=30)}
# Operator feeds (Phase 10): daily per configured operator, then a weekly calibration.
_ops = settings.config_dir / "ocpi_operators.yaml"
_operators = (yaml.safe_load(_ops.read_text()).get("operators") or {}) if _ops.exists() else {}
for _op in _operators:
    beat_schedule[f"ocpi-{_op}-daily"] = {
        "task": "ingest.ocpi",
        "schedule": crontab(hour=3, minute=0),
        "args": (_op,),
    }
if _operators:
    beat_schedule["calibration-weekly"] = {
        "task": "ingest.calibrate",
        "schedule": crontab(hour=4, minute=0, day_of_week=1),
    }
celery_app.conf.beat_schedule = beat_schedule
