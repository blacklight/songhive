"""
Streaming service: resolve uploads for streaming, handle range requests.
"""

from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.upload import Upload


async def get_upload_for_track(session: AsyncSession, track_id: str) -> Optional[Upload]:
    """Get the best available upload for a track."""
    result = await session.execute(select(Upload).where(Upload.track_id == track_id).limit(1))
    return result.scalar_one_or_none()
