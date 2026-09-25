"""The Celery app for RAGNOUS background jobs.

Kept in its own module so `celery -A app.workers.celery_app worker` and
`celery -A app.workers.celery_app beat` both bind to the same instance. Tasks
are registered via `include=` so a task file never has to import the app —
avoiding a circular import between the app and the task modules.

Broker/backend: Redis. `REDIS_URL` doubles as broker + result store; if unset
we fall back to a local Redis at 6379 so a dev machine "just works".
"""

from __future__ import annotations

import os

from celery import Celery
from celery.schedules import crontab


REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")


celery_app = Celery(
    "ragnous",
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=[
        "app.workers.tasks_reflection",
        "app.workers.tasks_export",
        "app.workers.tasks_tts",
    ],
)

celery_app.conf.update(
    task_time_limit=300,             # kill any task that runs > 5 min
    task_soft_time_limit=240,
    worker_prefetch_multiplier=1,    # LLM tasks aren't uniform — no batching
    task_acks_late=True,             # retry on worker death, at-least-once
    task_default_queue="default",
    task_track_started=True,
)

# Reflection runs nightly at 03:00 UTC — well past the tail of an Indian
# school day, so a student's last session of the evening is included.
celery_app.conf.beat_schedule = {
    "reflect-nightly": {
        "task": "app.workers.tasks_reflection.reflect_all_users",
        "schedule": crontab(minute=0, hour=3),
    },
}
