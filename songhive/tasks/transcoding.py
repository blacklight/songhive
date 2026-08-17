"""
Transcoding tasks: pre-transcode audio files to common formats.
"""

from .celery import celery_app


@celery_app.task(name="songhive.tasks.transcoding.transcode_upload")
def transcode_upload(upload_id: str, target_format: str, bitrate: str = "192k"):
    """
    Transcode an uploaded audio file to the specified format.
    Stores the result alongside the original.
    """
    # TODO: implement using Transcoder
