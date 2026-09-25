"""
Playlist routes.
"""

from datetime import datetime
from typing import List, Literal, Optional, Set

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from ...config.schema import SonghiveConfig
from ...models._enums import Visibility
from ...models.audit_log import AuditTargetType
from ...models.playlist import Playlist
from ...models.podcast import PodcastEpisode
from ...models.user import User
from ...services import acl, audit, collection, deletion, music
from ...services import podcasts as podcasts_service
from ...services import remote_content
from ...services.auth import get_user_by_username
from ...services.federation import unpublish_track_activity
from ...services.storage import StorageService
from ...services.tags import (
    add_tags_to_entity,
    remove_tag_from_entity,
    validate_tag_name,
)
from .._common import Pagination, client_ip, get_pagination
from .._include import IncludeQuery, get_include
from .._sorting import SortParams, get_sort
from ..deps import (
    get_config,
    get_current_user,
    get_current_user_optional,
    get_db,
    get_storage_service,
    require_access,
)
from ..middleware.rate_limit import rate_limit_account
from ..responses import TrackResponse, TrackSummary, UserSummary, _is_loaded, build_track_summary, build_user_summary
from ._common import TagListRequest
from ._images import remove_entity_image, upload_entity_image
from .remote import RemoteObjectResponse, _object_response, _playable_object_ids
from .tracks import _build_track_response

router = APIRouter(prefix="/playlists")


class PlaylistResponse(BaseModel):
    """Public playlist response."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    owner_id: Optional[str] = None
    description: Optional[str] = None
    visibility: str = Visibility.PRIVATE.value
    image_url: Optional[str] = None
    cover_url: Optional[str] = None
    in_collection: bool = False
    owner: Optional[UserSummary] = None
    tracks: Optional[List[TrackSummary]] = None
    tags: List[str] = []


class PlaylistStatsResponse(BaseModel):
    """Aggregate statistics for a playlist's accessible items."""

    track_count: int
    episode_count: int = 0
    total_duration: float


class PlaylistCreate(BaseModel):
    """Playlist creation payload."""

    name: str
    description: Optional[str] = None


class PlaylistUpdate(BaseModel):
    """Playlist partial update."""

    name: Optional[str] = None
    description: Optional[str] = None
    visibility: Optional[Visibility] = None


class AddPlaylistTracksRequest(BaseModel):
    """Request body for adding tracks, albums, artists, remote objects, or podcast episodes to a playlist."""

    track_ids: Optional[List[str]] = None
    album_id: Optional[str] = None
    artist_id: Optional[str] = None
    episode_ids: Optional[List[str]] = None
    # Adds every cataloged episode of the podcast, oldest first.
    podcast_id: Optional[str] = None
    # Cached ``remote_objects`` ids — track resources resolve to themselves,
    # remote containers (album/artist/library/playlist) expand to their
    # cached track descendants.
    remote_object_ids: Optional[List[str]] = None
    allow_duplicates: bool = False


class RemovePlaylistTracksRequest(BaseModel):
    """Request body for removing tracks, remote objects, or podcast episodes from a playlist."""

    track_ids: Optional[List[str]] = None
    episode_ids: Optional[List[str]] = None
    remote_object_ids: Optional[List[str]] = None


class ReorderPlaylistTracksRequest(BaseModel):
    """Request body for reordering items in a playlist.

    ``item_ids`` are entity ids — track ids or podcast episode ids — moved as
    a block to ``position``. ``track_ids`` is a deprecated alias for
    ``item_ids``.
    """

    item_ids: Optional[List[str]] = None
    track_ids: Optional[List[str]] = None
    position: Optional[int] = None

    @field_validator("position")
    @classmethod
    def _position_must_be_positive(cls, value: Optional[int]) -> Optional[int]:
        if value is not None and value <= 0:
            raise ValueError("position must be a positive integer")
        return value


class ReorderPlaylistTracksResponse(BaseModel):
    """Response body for a successful playlist item reorder."""

    reordered: bool = True
    item_ids: List[str]
    # Deprecated alias of ``item_ids`` kept for backward compatibility.
    track_ids: List[str]
    count: int


class PlaylistEpisodeItem(BaseModel):
    """Podcast episode data embedded in a playlist item."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    podcast_id: str
    podcast_title: str
    title: str
    description: Optional[str] = None
    link: Optional[str] = None
    image_url: Optional[str] = None
    audio_url: str
    audio_type: Optional[str] = None
    audio_length: Optional[int] = None
    duration_seconds: Optional[int] = None
    published_at: Optional[datetime] = None
    season_number: Optional[int] = None
    episode_number: Optional[int] = None
    episode_type: Optional[str] = None
    played: bool = False


class PlaylistItemResponse(BaseModel):
    """One ordered playlist entry — a track, a podcast episode, or a remote object."""

    item_id: str
    position: int
    type: Literal["track", "episode", "remote"]
    track: Optional[TrackResponse] = None
    episode: Optional[PlaylistEpisodeItem] = None
    remote: Optional[RemoteObjectResponse] = None


async def _playlist_image_url(playlist: Playlist, storage: StorageService) -> Optional[str]:
    """Resolve a playlist's image URL from its stored file."""
    if playlist.image_file_id and playlist.image_file:
        return await storage.get_url(playlist.image_file)
    return None


async def _playlist_cover_url(playlist: Playlist, storage: StorageService) -> Optional[str]:
    """Resolve a playlist's cover art URL from its stored file."""
    if playlist.cover_file_id and playlist.cover_file:
        return await storage.get_url(playlist.cover_file)
    return None


def _playlist_tags(playlist: Playlist) -> List[str]:
    """Return loaded tag names, avoiding a lazy load."""
    if not _is_loaded(playlist, "tags"):
        return []
    return [h.name for h in playlist.tags]


async def _build_playlist_response(
    playlist: Playlist,
    user: Optional[User],
    storage: StorageService,
    include: IncludeQuery,
    saved_ids: Optional[Set[str]] = None,
) -> PlaylistResponse:
    """Build a PlaylistResponse with optional nested summaries."""
    owner = None
    owner_id = playlist.owner_id
    if "owner" in include and owner_id is not None and playlist.owner:
        owner = await build_user_summary(playlist.owner)
    tracks = None
    if "tracks" in include:
        playlist_tracks = getattr(playlist, "tracks", None)
        if playlist_tracks is not None:
            track_list: List[TrackSummary] = []
            for pt in sorted(playlist_tracks, key=lambda pt: pt.position):
                summary = await build_track_summary(pt.track, storage)
                if summary is not None:
                    track_list.append(summary)
            tracks = track_list

    return PlaylistResponse(
        id=str(playlist.id),
        name=playlist.name,
        owner_id=owner_id,
        description=playlist.description,
        visibility=playlist.visibility,
        image_url=await _playlist_image_url(playlist, storage),
        cover_url=await _playlist_cover_url(playlist, storage),
        in_collection=user is not None
        and (playlist.owner_id == user.id or (saved_ids is not None and str(playlist.id) in saved_ids)),
        owner=owner,
        tracks=tracks,
        tags=_playlist_tags(playlist),
    )


@router.get("/", response_model=List[PlaylistResponse])
async def list_playlists(
    response: Response,
    q: Optional[str] = Query(None, description="Search playlists"),
    owner_username: Optional[str] = Query(None, description="Filter by owner's username"),
    collection_only: Optional[bool] = Query(
        None,
        alias="collection",
        description="Only return playlists in the current user's collection (owned or saved)",
    ),
    user: Optional[User] = Depends(get_current_user_optional),
    pagination: Pagination = Depends(get_pagination),
    sort: SortParams = Depends(get_sort({"name", "created_at", "updated_at"}, "name")),
    db: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage_service),
    include: IncludeQuery = Depends(get_include({"owner", "tracks", "tags"})),
):
    """List playlists visible to the requester."""
    if owner_username:
        owner = await get_user_by_username(db, owner_username)
        if owner is None or not owner.is_active:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
        owner_id = str(owner.id)
    else:
        owner_id = None

    total = await music.count_playlists(db, user=user, owner_id=owner_id, query=q, collection=collection_only)
    rows = await music.list_playlists(
        db,
        user=user,
        owner_id=owner_id,
        query=q,
        collection=collection_only,
        limit=pagination.limit,
        offset=pagination.offset,
        include=set(include.values),
        sort_by=sort.field,
        sort_dir=sort.direction,
    )
    pagination.set_total(response, total)
    saved_ids = await collection.saved_item_ids(db, user, "playlist", [str(p.id) for p in rows])
    return [await _build_playlist_response(p, user, storage, include, saved_ids) for p in rows]


@router.post("/", response_model=PlaylistResponse, status_code=201)
async def create_playlist(
    body: PlaylistCreate,
    visibility: Visibility = Query(Visibility.PRIVATE),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new playlist owned by the current user."""
    playlist = Playlist(
        name=body.name,
        owner_id=current_user.id,
        description=body.description,
        visibility=visibility.value,
    )
    db.add(playlist)
    await db.commit()

    return PlaylistResponse(
        id=str(playlist.id),
        name=playlist.name,
        owner_id=playlist.owner_id,
        description=playlist.description,
        visibility=playlist.visibility,
        in_collection=True,
    )


@router.get(
    "/{playlist_id}",
    response_model=PlaylistResponse,
    dependencies=[Depends(require_access("playlist"))],
)
async def get_playlist(
    playlist_id: str,
    user: Optional[User] = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage_service),
    include: IncludeQuery = Depends(get_include({"owner", "tracks", "tags"})),
):
    """Get a playlist by ID."""
    playlist = await music.get_playlist(db, playlist_id, include=set(include.values))
    # ``require_access`` already loads the row and raises 404 when missing.
    if playlist is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Playlist not found")

    saved_ids = await collection.saved_item_ids(db, user, "playlist", {playlist_id})
    return await _build_playlist_response(playlist, user, storage, include, saved_ids)


@router.get(
    "/{playlist_id}/stats",
    response_model=PlaylistStatsResponse,
    dependencies=[Depends(require_access("playlist"))],
)
async def get_playlist_stats(
    playlist_id: str,
    user: Optional[User] = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    """Return aggregate track count and total duration for a playlist."""
    playlist = await music.get_playlist(db, playlist_id)
    if playlist is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Playlist not found")

    return await music.get_playlist_stats(db, playlist_id, user=user)


@router.patch("/{playlist_id}", response_model=PlaylistResponse)
async def update_playlist(
    playlist_id: str,
    body: PlaylistUpdate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage_service),
    include: IncludeQuery = Depends(get_include({"owner", "tracks", "tags"})),
):
    """Partially update a playlist."""
    playlist = await music.get_playlist(db, playlist_id, include=set(include.values))
    if playlist is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    if not await acl.can_manage(db, current_user, "playlist", playlist_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    if body.name is not None:
        playlist.name = body.name
    if body.description is not None:
        playlist.description = body.description
    if body.visibility is not None:
        playlist.visibility = body.visibility.value

    await audit.log_action(
        db,
        actor_id=current_user.id,
        action="playlist.update",
        target_type=AuditTargetType.PLAYLIST,
        target_id=playlist_id,
        details={
            "name": playlist.name,
            "visibility": playlist.visibility,
            "image_file_id": playlist.image_file_id,
            "cover_file_id": playlist.cover_file_id,
        },
        ip_address=client_ip(request),
    )
    await db.commit()

    saved_ids = await collection.saved_item_ids(db, current_user, "playlist", {playlist_id})
    return await _build_playlist_response(playlist, current_user, storage, include, saved_ids)


@router.post("/{playlist_id}/image", response_model=PlaylistResponse)
async def upload_playlist_image(
    playlist_id: str,
    request: Request,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage_service),
    include: IncludeQuery = Depends(get_include({"owner", "tracks", "tags"})),
):
    """Upload a playlist image."""
    playlist = await music.get_playlist(db, playlist_id, include=set(include.values))
    if playlist is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    if not await acl.can_manage(db, current_user, "playlist", playlist_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    stored = await upload_entity_image(
        db,
        storage,
        playlist,
        "image_file_id",
        file,
        current_user,
        owner_id=playlist.owner_id,
    )

    await audit.log_action(
        db,
        actor_id=current_user.id,
        action="playlist.update",
        target_type=AuditTargetType.PLAYLIST,
        target_id=playlist_id,
        details={"image_file_id": stored.id},
        ip_address=client_ip(request),
    )
    await db.commit()

    saved_ids = await collection.saved_item_ids(db, current_user, "playlist", {playlist_id})
    return await _build_playlist_response(playlist, current_user, storage, include, saved_ids)


@router.post("/{playlist_id}/cover", response_model=PlaylistResponse)
async def upload_playlist_cover(
    playlist_id: str,
    request: Request,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage_service),
    include: IncludeQuery = Depends(get_include({"owner", "tracks", "tags"})),
):
    """Upload playlist cover art."""
    playlist = await music.get_playlist(db, playlist_id, include=set(include.values))
    if playlist is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    if not await acl.can_manage(db, current_user, "playlist", playlist_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    stored = await upload_entity_image(
        db,
        storage,
        playlist,
        "cover_file_id",
        file,
        current_user,
        owner_id=playlist.owner_id,
    )

    await audit.log_action(
        db,
        actor_id=current_user.id,
        action="playlist.update",
        target_type=AuditTargetType.PLAYLIST,
        target_id=playlist_id,
        details={"cover_file_id": stored.id},
        ip_address=client_ip(request),
    )
    await db.commit()

    saved_ids = await collection.saved_item_ids(db, current_user, "playlist", {playlist_id})
    return await _build_playlist_response(playlist, current_user, storage, include, saved_ids)


@router.delete("/{playlist_id}/image", response_model=PlaylistResponse)
async def delete_playlist_image(
    playlist_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage_service),
    include: IncludeQuery = Depends(get_include({"owner", "tracks", "tags"})),
):
    """Remove a playlist image."""
    playlist = await music.get_playlist(db, playlist_id, include=set(include.values))
    if playlist is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    if not await acl.can_manage(db, current_user, "playlist", playlist_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    await remove_entity_image(playlist, "image_file_id")

    await audit.log_action(
        db,
        actor_id=current_user.id,
        action="playlist.update",
        target_type=AuditTargetType.PLAYLIST,
        target_id=playlist_id,
        details={"image_file_id": None},
        ip_address=client_ip(request),
    )
    await db.commit()

    saved_ids = await collection.saved_item_ids(db, current_user, "playlist", {playlist_id})
    return await _build_playlist_response(playlist, current_user, storage, include, saved_ids)


@router.delete("/{playlist_id}/cover", response_model=PlaylistResponse)
async def delete_playlist_cover(
    playlist_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage_service),
    include: IncludeQuery = Depends(get_include({"owner", "tracks", "tags"})),
):
    """Remove playlist cover art."""
    playlist = await music.get_playlist(db, playlist_id, include=set(include.values))
    if playlist is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    if not await acl.can_manage(db, current_user, "playlist", playlist_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    await remove_entity_image(playlist, "cover_file_id")

    await audit.log_action(
        db,
        actor_id=current_user.id,
        action="playlist.update",
        target_type=AuditTargetType.PLAYLIST,
        target_id=playlist_id,
        details={"cover_file_id": None},
        ip_address=client_ip(request),
    )
    await db.commit()

    saved_ids = await collection.saved_item_ids(db, current_user, "playlist", {playlist_id})
    return await _build_playlist_response(playlist, current_user, storage, include, saved_ids)


async def _resolve_track_ids(
    db: AsyncSession,
    user: User,
    body: AddPlaylistTracksRequest,
) -> List[str]:
    """Resolve ``track_ids`` / ``album_id`` / ``artist_id`` into accessible track IDs."""
    resolved: List[str] = []

    if body.track_ids:
        accessible = await acl.filter_accessible_track_ids(db, user, body.track_ids)
        resolved.extend(track_id for track_id in body.track_ids if track_id in accessible)

    if body.album_id:
        resolved.extend(await music.get_track_ids_for_album(db, body.album_id, user=user))
    if body.artist_id:
        resolved.extend(await music.get_track_ids_for_artist(db, body.artist_id, user=user))

    seen: set[str] = set()
    deduped: List[str] = []
    for track_id in resolved:
        if track_id not in seen:
            seen.add(track_id)
            deduped.append(track_id)
    return deduped


async def _resolve_episode_ids(
    db: AsyncSession,
    body: AddPlaylistTracksRequest,
) -> List[str]:
    """Resolve ``episode_ids`` / ``podcast_id`` into cataloged episode IDs."""
    resolved: List[str] = []

    if body.episode_ids:
        existing = await podcasts_service.existing_episode_ids(db, body.episode_ids)
        resolved.extend(episode_id for episode_id in body.episode_ids if episode_id in existing)

    if body.podcast_id:
        resolved.extend(await podcasts_service.episode_ids_for_podcast(db, body.podcast_id))

    seen: set[str] = set()
    deduped: List[str] = []
    for episode_id in resolved:
        if episode_id not in seen:
            seen.add(episode_id)
            deduped.append(episode_id)
    return deduped


async def _track_response(
    storage: StorageService,
    track,
    user: Optional[User],
    include: IncludeQuery,
    favorited_track_ids: Optional[Set[str]] = None,
) -> TrackResponse:
    """Build a TrackResponse with audio URL."""
    return await _build_track_response(track, user, storage, include, favorited_track_ids)


@router.post("/{playlist_id}/tracks", status_code=status.HTTP_201_CREATED)
async def add_tracks_to_playlist(
    playlist_id: str,
    request: Request,
    body: AddPlaylistTracksRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    config: SonghiveConfig = Depends(get_config),
):
    """Add existing tracks, an album, an artist, episodes, or a whole podcast to a playlist."""
    playlist = await music.get_playlist(db, playlist_id)
    if playlist is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if not await acl.can_manage(db, current_user, "playlist", playlist_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    if not any(
        (
            body.track_ids,
            body.album_id,
            body.artist_id,
            body.episode_ids,
            body.podcast_id,
            body.remote_object_ids,
        )
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="At least one source must be provided",
        )
    if (body.episode_ids or body.podcast_id) and not config.podcasts.enabled:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Podcasts are disabled on this instance",
        )

    track_ids = await _resolve_track_ids(db, current_user, body)
    episode_ids = await _resolve_episode_ids(db, body)
    remote_object_ids = await remote_content.resolve_remote_track_ids(db, config, body.remote_object_ids or [])
    try:
        added_track_ids, added_episode_ids, added_remote_ids = await music.add_playlist_items(
            db,
            playlist_id,
            track_ids=track_ids,
            episode_ids=episode_ids,
            remote_object_ids=remote_object_ids,
            allow_duplicates=body.allow_duplicates,
        )
    except music.DuplicatePlaylistTrackError as exc:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={
                "detail": "Items already in playlist",
                "track_ids": exc.track_ids,
                "episode_ids": exc.episode_ids,
                "remote_object_ids": exc.remote_object_ids,
            },
        )

    await audit.log_action(
        db,
        actor_id=current_user.id,
        action="playlist_track.add",
        target_type=AuditTargetType.PLAYLIST,
        target_id=playlist_id,
        details={
            "source": body.model_dump(exclude_unset=True),
            "track_ids": added_track_ids,
            "episode_ids": added_episode_ids,
            "remote_object_ids": added_remote_ids,
            "count": len(added_track_ids) + len(added_episode_ids) + len(added_remote_ids),
        },
        ip_address=client_ip(request),
    )
    await db.commit()

    return {
        "added": len(added_track_ids) + len(added_episode_ids) + len(added_remote_ids),
        "track_ids": added_track_ids,
        "episode_ids": added_episode_ids,
        "remote_object_ids": added_remote_ids,
    }


@router.get(
    "/{playlist_id}/tracks",
    response_model=List[TrackResponse],
    dependencies=[Depends(require_access("playlist"))],
)
async def list_playlist_tracks_route(
    response: Response,
    playlist_id: str,
    q: Optional[str] = Query(None, description="Search tracks by title, artist, album, tag, or genre"),
    user: Optional[User] = Depends(get_current_user_optional),
    pagination: Pagination = Depends(get_pagination),
    sort: SortParams = Depends(
        get_sort(
            {
                "position",
                "created_at",
                "title",
                "artist_name",
                "album_title",
                "updated_at",
                "release_year",
            },
            "position",
        )
    ),
    db: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage_service),
    include: IncludeQuery = Depends(get_include({"artist", "album", "owner"})),
):
    """List tracks that are members of the playlist."""
    playlist = await music.get_playlist(db, playlist_id)
    if playlist is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    total = await music.count_playlist_tracks(db, playlist_id=playlist_id, user=user, query=q)
    rows = await music.list_playlist_tracks(
        db,
        playlist_id=playlist_id,
        user=user,
        limit=pagination.limit,
        offset=pagination.offset,
        include=set(include.values),
        sort_by=sort.field,
        sort_dir=sort.direction,
        query=q,
    )
    pagination.set_total(response, total)
    favorited_ids = await music.get_favorited_track_ids(
        db,
        user,
        {str(row.id) for row in rows},
    )
    return [await _track_response(storage, row, user, include, favorited_ids) for row in rows]


def _episode_item(episode: PodcastEpisode, played: bool) -> PlaylistEpisodeItem:
    """Build the episode side of a playlist item (podcast must be loaded)."""
    podcast = episode.podcast
    return PlaylistEpisodeItem(
        id=str(episode.id),
        podcast_id=str(episode.podcast_id),
        podcast_title=podcast.title if podcast is not None else "",
        title=episode.title,
        description=episode.description,
        link=episode.link,
        image_url=episode.image_url or (podcast.image_url if podcast is not None else None),
        audio_url=episode.audio_url,
        audio_type=episode.audio_type,
        audio_length=episode.audio_length,
        duration_seconds=episode.duration_seconds,
        published_at=episode.published_at,
        season_number=episode.season_number,
        episode_number=episode.episode_number,
        episode_type=episode.episode_type,
        played=played,
    )


@router.get(
    "/{playlist_id}/items",
    response_model=List[PlaylistItemResponse],
    dependencies=[Depends(require_access("playlist"))],
)
async def list_playlist_items_route(
    response: Response,
    playlist_id: str,
    q: Optional[str] = Query(None, description="Search items by title, artist, album, podcast, tag, or genre"),
    user: Optional[User] = Depends(get_current_user_optional),
    pagination: Pagination = Depends(get_pagination),
    sort: SortParams = Depends(
        get_sort(
            {
                "position",
                "created_at",
                "title",
                "artist_name",
                "album_title",
                "updated_at",
                "release_year",
            },
            "position",
        )
    ),
    db: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage_service),
    include: IncludeQuery = Depends(get_include({"artist", "album", "owner"})),
    config: SonghiveConfig = Depends(get_config),
):
    """List a playlist's items — tracks and podcast episodes — in order."""
    playlist = await music.get_playlist(db, playlist_id)
    if playlist is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    total = await music.count_playlist_items(db, playlist_id=playlist_id, user=user, query=q)
    rows = await music.list_playlist_items(
        db,
        playlist_id=playlist_id,
        user=user,
        limit=pagination.limit,
        offset=pagination.offset,
        include=set(include.values),
        sort_by=sort.field,
        sort_dir=sort.direction,
        query=q,
    )
    pagination.set_total(response, total)

    track_ids = {str(row.track.id) for row in rows if row.track is not None}
    episode_ids = [str(row.episode.id) for row in rows if row.episode is not None]
    remote_rows = [row.remote_object for row in rows if row.remote_object is not None]
    favorited_ids = await music.get_favorited_track_ids(db, user, track_ids)
    played_ids = await podcasts_service.played_episode_ids(db, user, episode_ids) if user is not None else set()
    playable_remote = await _playable_object_ids(db, remote_rows)
    remote_favorited = await remote_content.get_favorited_remote_object_ids(
        db, user, {str(row.id) for row in remote_rows}
    )
    remote_actor_handles = await remote_content.resolve_actor_handle_map(config, [row.actor_url for row in remote_rows])

    items: List[PlaylistItemResponse] = []
    for row in rows:
        if row.track is not None:
            items.append(
                PlaylistItemResponse(
                    item_id=str(row.id),
                    position=row.position,
                    type="track",
                    track=await _track_response(storage, row.track, user, include, favorited_ids),
                )
            )
        elif row.episode is not None:
            items.append(
                PlaylistItemResponse(
                    item_id=str(row.id),
                    position=row.position,
                    type="episode",
                    episode=_episode_item(row.episode, str(row.episode.id) in played_ids),
                )
            )
        elif row.remote_object is not None:
            remote_row = row.remote_object
            items.append(
                PlaylistItemResponse(
                    item_id=str(row.id),
                    position=row.position,
                    type="remote",
                    remote=_object_response(
                        remote_row,
                        favorited=str(remote_row.id) in remote_favorited,
                        playable=str(remote_row.id) in playable_remote,
                        actor_handles=remote_actor_handles,
                    ),
                )
            )
    return items


@router.post("/{playlist_id}/tracks/remove", status_code=status.HTTP_200_OK)
async def remove_tracks_from_playlist(
    playlist_id: str,
    request: Request,
    body: RemovePlaylistTracksRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Remove existing tracks, remote objects, or podcast episodes from a playlist."""
    playlist = await music.get_playlist(db, playlist_id)
    if playlist is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if not await acl.can_manage(db, current_user, "playlist", playlist_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    if not body.track_ids and not body.episode_ids and not body.remote_object_ids:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="track_ids, episode_ids, or remote_object_ids must not be empty",
        )

    (
        removed_count,
        removed_track_ids,
        removed_episode_ids,
        removed_remote_ids,
    ) = await music.remove_playlist_items(
        db,
        playlist_id,
        track_ids=body.track_ids or [],
        episode_ids=body.episode_ids or [],
        remote_object_ids=body.remote_object_ids or [],
    )

    await audit.log_action(
        db,
        actor_id=current_user.id,
        action="playlist_track.remove",
        target_type=AuditTargetType.PLAYLIST,
        target_id=playlist_id,
        details={
            "track_ids": removed_track_ids,
            "episode_ids": removed_episode_ids,
            "remote_object_ids": removed_remote_ids,
            "count": removed_count,
        },
        ip_address=client_ip(request),
    )
    await db.commit()

    return {
        "removed": removed_count,
        "track_ids": removed_track_ids,
        "episode_ids": removed_episode_ids,
        "remote_object_ids": removed_remote_ids,
    }


@router.post("/{playlist_id}/tracks/reorder", response_model=ReorderPlaylistTracksResponse)
async def reorder_playlist_tracks_route(
    playlist_id: str,
    request: Request,
    body: ReorderPlaylistTracksRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Reorder items within a playlist (``item_ids`` may mix tracks and episodes)."""
    playlist = await music.get_playlist(db, playlist_id)
    if playlist is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    if not await acl.can_manage(db, current_user, "playlist", playlist_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    item_ids = list(body.item_ids or [])
    for track_id in body.track_ids or []:
        if track_id not in item_ids:
            item_ids.append(track_id)
    if not item_ids:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="item_ids must not be empty",
        )

    try:
        moved = await music.reorder_playlist_items(
            db,
            playlist_id,
            item_ids,
            body.position,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc

    await audit.log_action(
        db,
        actor_id=current_user.id,
        action="playlist_track.reorder",
        target_type=AuditTargetType.PLAYLIST,
        target_id=playlist_id,
        details={
            "item_ids": moved,
            "track_ids": moved,
            "position": body.position,
            "count": len(moved),
        },
        ip_address=client_ip(request),
    )
    await db.commit()

    return ReorderPlaylistTracksResponse(reordered=True, item_ids=moved, track_ids=moved, count=len(moved))


@router.delete("/{playlist_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(rate_limit_account)])
async def delete_playlist(
    playlist_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    recursive: bool = Query(False, description="Also delete the playlist's tracks and uploads"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage_service),
):
    """Delete a playlist and, optionally, its tracks."""
    playlist = await music.get_playlist(db, playlist_id)
    if playlist is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    if not await acl.can_manage(db, current_user, "playlist", playlist_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    admin_deletion = current_user.is_admin and playlist.owner_id != current_user.id
    await audit.log_action(
        db,
        actor_id=current_user.id,
        action="playlist.admin_delete" if admin_deletion else "playlist.delete",
        target_type=AuditTargetType.PLAYLIST,
        target_id=playlist_id,
        details={
            "name": playlist.name,
            "recursive": recursive,
            "owner_id": playlist.owner_id,
        },
        ip_address=client_ip(request),
    )

    try:
        result = await deletion.delete_playlist(
            db,
            storage,
            playlist_id,
            recursive=recursive,
            user=current_user,
            is_admin=current_user.is_admin,
        )
    except deletion.DeletionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.args[0]) from exc

    await db.commit()

    for info in result.unpublish:
        if info.owner is not None and info.artist is not None:
            background_tasks.add_task(
                unpublish_track_activity,
                info.track,
                info.artist,
                info.owner,
                request.app.state.config,
                info.federation_object_id,
            )

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{playlist_id}/tags", response_model=PlaylistResponse)
async def add_playlist_tags(
    playlist_id: str,
    request: Request,
    body: TagListRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage_service),
    include: IncludeQuery = Depends(get_include({"owner", "tracks", "tags"})),
):
    """Add tags to a playlist."""
    playlist = await music.get_playlist(db, playlist_id, include={"owner"})
    if playlist is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Playlist not found")

    if not await acl.can_manage(db, current_user, "playlist", playlist_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    try:
        await add_tags_to_entity(
            db,
            "playlist",
            playlist_id,
            body.tags,
            user_id=current_user.id,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    playlist = await music.get_playlist(db, playlist_id, include=set(include.values) | {"owner", "tags"})
    if playlist is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Playlist not found")
    await audit.log_action(
        db,
        actor_id=current_user.id,
        action="tag.add",
        target_type=AuditTargetType.PLAYLIST,
        target_id=playlist_id,
        details={"tags": [validate_tag_name(h) for h in body.tags]},
        ip_address=client_ip(request),
    )
    await db.commit()
    saved_ids = await collection.saved_item_ids(db, current_user, "playlist", {playlist_id})
    return await _build_playlist_response(playlist, current_user, storage, include, saved_ids)


@router.delete("/{playlist_id}/tags/{tag}", response_model=PlaylistResponse)
async def remove_playlist_tag(
    playlist_id: str,
    tag: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage_service),
    include: IncludeQuery = Depends(get_include({"owner", "tracks", "tags"})),
):
    """Remove a tag from a playlist."""
    playlist = await music.get_playlist(db, playlist_id, include={"owner"})
    if playlist is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Playlist not found")

    if not await acl.can_manage(db, current_user, "playlist", playlist_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    try:
        await remove_tag_from_entity(db, "playlist", playlist_id, tag)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    playlist = await music.get_playlist(db, playlist_id, include=set(include.values) | {"owner", "tags"})
    if playlist is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Playlist not found")
    await audit.log_action(
        db,
        actor_id=current_user.id,
        action="tag.remove",
        target_type=AuditTargetType.PLAYLIST,
        target_id=playlist_id,
        details={"tag": validate_tag_name(tag)},
        ip_address=client_ip(request),
    )
    await db.commit()
    saved_ids = await collection.saved_item_ids(db, current_user, "playlist", {playlist_id})
    return await _build_playlist_response(playlist, current_user, storage, include, saved_ids)
