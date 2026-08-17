"""
Celery application factory.
"""

from celery import Celery


def make_celery(
    broker_url: str = "redis://localhost:6379/1",
    result_backend: str = "redis://localhost:6379/2",
) -> Celery:
    """Create and configure a Celery application."""
    app = Celery("songhive")
    app.conf.update(
        broker_url=broker_url,
        result_backend=result_backend,
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        timezone="UTC",
        enable_utc=True,
        task_routes={
            "songhive.tasks.import_.*": {"queue": "import"},
            "songhive.tasks.federation.*": {"queue": "federation"},
            "songhive.tasks.transcoding.*": {"queue": "transcoding"},
        },
    )
    app.autodiscover_tasks(["songhive.tasks"])
    return app


celery_app = make_celery()
