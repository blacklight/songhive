"""
Cascade helpers for user deletion — extracted from ``manager.py`` to keep
the account-lifecycle module focused.
"""

from typing import List

from sqlalchemy import delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.album import Album
from ..models.api_token import ApiToken
from ..models.audit_log import AuditLog
from ..models.favorite import Favorite
from ..models.history import ListeningHistory
from ..models.invite import Invite
from ..models.library import Library
from ..models.library_track import LibraryTrack
from ..models.mention_record import MentionRecord
from ..models.notification import ActivitySubscription, Notification, NotificationPreference
from ..models.oauth_client import OAuth2Client
from ..models.playlist import Playlist, PlaylistTrack
from ..models.podcast import PodcastEpisodePlay, PodcastSubscription, PodcastSyncConfig, PodcastSyncEvent
from ..models.radio import Radio
from ..models.report import Report
from ..models.setting import Setting
from ..models.share_grant import ShareGrant
from ..models.share_token import ShareToken
from ..models.stored_file import StoredFile
from ..models.track import Track
from ..models.two_factor import RecoveryCode, WebAuthnCredential
from ..models.user import User
from ..models.user_link import UserLink
from ..services import deletion
from ..services import notifications as notifications_service
from ..services.storage import StorageService


async def remove_user_references(session: AsyncSession, user: User) -> None:
    """Remove or nullify all database references to a user before deletion.

    This ensures the user row can be deleted even on databases (such as
    SQLite without foreign-key enforcement) that do not automatically apply
    ``ON DELETE`` actions.
    """
    # Remove rows that cannot exist without a referencing user.
    await session.execute(delete(ApiToken).where(ApiToken.user_id == user.id))
    await session.execute(delete(Favorite).where(Favorite.user_id == user.id))
    await session.execute(delete(ListeningHistory).where(ListeningHistory.user_id == user.id))
    await session.execute(delete(MentionRecord).where(MentionRecord.user_id == user.id))
    await session.execute(delete(Notification).where(Notification.user_id == user.id))
    await session.execute(delete(NotificationPreference).where(NotificationPreference.user_id == user.id))
    await session.execute(delete(PodcastEpisodePlay).where(PodcastEpisodePlay.user_id == user.id))
    await session.execute(delete(PodcastSubscription).where(PodcastSubscription.user_id == user.id))
    await session.execute(delete(PodcastSyncEvent).where(PodcastSyncEvent.user_id == user.id))
    await session.execute(delete(PodcastSyncConfig).where(PodcastSyncConfig.user_id == user.id))
    # Subscriptions in both directions: ones the user made and ones
    # targeting them — including any keyed on their actor URL.
    conditions = [
        ActivitySubscription.user_id == user.id,
        ActivitySubscription.target_user_id == user.id,
    ]
    if user.actor_url:
        conditions.append(ActivitySubscription.target_actor_url == user.actor_url)
    await session.execute(delete(ActivitySubscription).where(or_(*conditions)))
    # Notifications and mention records the user produced on other accounts
    # (likes, shares, follows, mentions) are no longer applicable once the
    # actor is gone.
    from ..services import mention_records as mention_records_service

    await mention_records_service.delete_mentions(
        session,
        actor_urls=[  # type: ignore
            u
            for u in (
                user.actor_url,
                f"/users/{user.username}",
                f"urn:songhive:user:{user.username}",
            )
            if u
        ],
    )
    await notifications_service.retract_notifications(
        session,
        actor_urls=[  # type: ignore
            u
            for u in (
                user.actor_url,
                f"/users/{user.username}",
                f"urn:songhive:user:{user.username}",
            )
            if u
        ],
    )
    await session.execute(delete(Invite).where(Invite.created_by == user.id))
    await session.execute(delete(Report).where(Report.reporter_id == user.id))
    await session.execute(delete(ShareGrant).where(ShareGrant.user_id == user.id))
    await session.execute(delete(ShareGrant).where(ShareGrant.created_by == user.id))
    await session.execute(delete(ShareToken).where(ShareToken.created_by == user.id))
    await session.execute(delete(UserLink).where(UserLink.user_id == user.id))
    await session.execute(delete(WebAuthnCredential).where(WebAuthnCredential.user_id == user.id))
    await session.execute(delete(RecoveryCode).where(RecoveryCode.user_id == user.id))

    # Payment rows owned by the user. Sales, the seller connect account, and
    # the instance subscription go with the account; orders and entitlements
    # keep their rows (with the buyer nulled) as the sellers' revenue ledger.
    from ..models.payments import (
        ConnectedAccount,
        InstanceSubscription,
        PaymentOrder,
        PurchaseEntitlement,
        Sale,
    )

    await session.execute(delete(Sale).where(Sale.owner_id == user.id))
    await session.execute(delete(ConnectedAccount).where(ConnectedAccount.user_id == user.id))
    await session.execute(delete(InstanceSubscription).where(InstanceSubscription.user_id == user.id))
    await session.execute(update(PaymentOrder).where(PaymentOrder.buyer_user_id == user.id).values(buyer_user_id=None))
    await session.execute(
        update(PurchaseEntitlement).where(PurchaseEntitlement.user_id == user.id).values(user_id=None)
    )

    # Nullify optional owner/reviewer fields on user-created content.
    await session.execute(update(Album).where(Album.owner_id == user.id).values(owner_id=None))
    await session.execute(update(AuditLog).where(AuditLog.actor_id == user.id).values(actor_id=None))
    await session.execute(update(Library).where(Library.owner_id == user.id).values(owner_id=None))
    await session.execute(update(LibraryTrack).where(LibraryTrack.added_by_id == user.id).values(added_by_id=None))
    await session.execute(update(OAuth2Client).where(OAuth2Client.owner_id == user.id).values(owner_id=None))
    await session.execute(update(Playlist).where(Playlist.owner_id == user.id).values(owner_id=None))
    await session.execute(update(PlaylistTrack).where(PlaylistTrack.added_by_id == user.id).values(added_by_id=None))
    await session.execute(update(Radio).where(Radio.owner_id == user.id).values(owner_id=None))
    await session.execute(update(Report).where(Report.reviewed_by == user.id).values(reviewed_by=None))
    await session.execute(update(Setting).where(Setting.updated_by == user.id).values(updated_by=None))
    await session.execute(update(StoredFile).where(StoredFile.owner_id == user.id).values(owner_id=None))
    await session.execute(update(Track).where(Track.owner_id == user.id).values(owner_id=None))


async def delete_user_content(
    session: AsyncSession,
    storage: StorageService,
    user: User,
) -> List[deletion.UnpublishInfo]:
    """Cascade-delete a user's content; returns unpublish records."""
    unpublish: List[deletion.UnpublishInfo] = []

    # Remove the user's radios first; they reference the user directly.
    await session.execute(delete(Radio).where(Radio.owner_id == user.id))

    # Remove user-owned playlists and libraries without deleting their tracks.
    playlist_ids = list(
        (await session.execute(select(Playlist.id).where(Playlist.owner_id == user.id))).scalars().all()
    )
    for playlist_id in playlist_ids:
        try:
            await deletion.delete_playlist(session, storage, str(playlist_id), recursive=False)
        except deletion.DeletionError as exc:
            if exc.status_code != 404:
                raise

    library_ids = list((await session.execute(select(Library.id).where(Library.owner_id == user.id))).scalars().all())
    for library_id in library_ids:
        try:
            await deletion.delete_library(session, storage, str(library_id), recursive=False)
        except deletion.DeletionError as exc:
            if exc.status_code != 404:
                raise

    # Delete all tracks owned by the user.
    track_ids = list((await session.execute(select(Track.id).where(Track.owner_id == user.id))).scalars().all())
    if track_ids:
        track_unpublish, _ = await deletion.delete_tracks_bulk(
            session,
            storage,
            [str(track_id) for track_id in track_ids],
            user=user,
        )
        unpublish.extend(track_unpublish)

    # Delete any remaining albums owned by the user.
    album_ids = list((await session.execute(select(Album.id).where(Album.owner_id == user.id))).scalars().all())
    for album_id in album_ids:
        try:
            await deletion.delete_album(
                session,
                storage,
                str(album_id),
                recursive=False,
                user=user,
                is_admin=False,
            )
        except deletion.DeletionError as exc:
            if exc.status_code != 404:
                raise
    return unpublish
