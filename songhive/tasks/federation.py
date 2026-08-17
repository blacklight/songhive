"""
Federation tasks: deliver activities to remote instances.
"""

from .celery import celery_app


@celery_app.task(name="songhive.tasks.federation.deliver_activity")
def deliver_activity(activity: dict, inbox_url: str, actor_key_id: str):
    """
    Deliver an ActivityPub activity to a remote inbox.
    Signs the request with HTTP signatures via pubby.
    """
    # TODO: implement using pubby's delivery mechanisms


@celery_app.task(name="songhive.tasks.federation.process_incoming")
def process_incoming(activity: dict):
    """
    Process an incoming ActivityPub activity (e.g., Follow, Like).
    """
    # TODO: implement activity processing
