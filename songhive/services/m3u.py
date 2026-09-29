"""
Extended-M3U export for albums and playlists.

The generated document carries ``#EXTINF`` metadata (``Artist - Title``,
duration in seconds) for every entry. Local tracks point at the instance's
streaming endpoint (``/api/v1/stream/{id}``), podcast episodes keep their
remote enclosure URL, and cached remote (federated) objects point at the
play-time resolving ``/api/v1/remote/objects/{id}/stream`` redirect.

M3U consumers (VLC, mpv, Subsonic clients, …) fetch the listed URLs
anonymously — no cookies or headers can be attached — so each emitted entry
must be playable without a session:

- ``access="exclude"`` (default) emits only anonymously playable entries:
  public tracks, member tracks of a public container (the ACL derives their
  access from it), public remote objects, and podcast episodes. Everything
  else is dropped and reported in the ``skipped`` count.
- ``access="token"`` additionally embeds a share token in the stream URL of
  tracks that are not anonymously playable. A caller-supplied share token
  (``?token=``, ``X-Share-Token``, or the ``share_token`` cookie) valid for
  the container is reused as-is; otherwise a fresh revocable
  ``share_tokens`` row is minted for the requesting owner/admin. Non-public
  remote objects are always dropped — the remote ACL has no token support.
"""

from dataclasses import dataclass
from typing import Awaitable, Callable, List, Optional, Tuple
from urllib.parse import quote

from sqlalchemy.ext.asyncio import AsyncSession

from ..config.schema import SonghiveConfig
from ..models._enums import Visibility
from ..models.album import Album
from ..models.playlist import Playlist
from ..models.podcast import PodcastEpisode
from ..models.remote_object import RemoteObject
from ..models.track import Track
from ..models.user import User
from . import acl, music, remote_content, sharing, streaming
from .downloads import sanitize_member_name

M3U_MEDIA_TYPE = "audio/x-mpegurl"
M3U_MAX_ENTRIES = 1000

# Remote objects embeddable in an M3U only when this resource kind is
# playable through the remote stream redirect.
_REMOTE_MEDIA_TYPES = ("Audio", "Track", "Video")

#: Resolves lazily to the raw share token embedded in track stream URLs, or
#: ``None`` when no token is available/needed yet.
Embedder = Callable[[], Awaitable[Optional[str]]]


class M3uExportError(ValueError):
    """An M3U export request could not be fulfilled (mapped to an HTTP error)."""

    def __init__(self, message: str, *, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


@dataclass
class M3uEntry:
    """One playable entry in the exported document."""

    url: str
    title: str
    artist: str = ""
    duration: Optional[float] = None


def _one_line(value: Optional[str]) -> str:
    """Collapse whitespace so a label never breaks an M3U line."""
    if not value:
        return ""
    return " ".join(str(value).split())


def render_document(name: str, entries: List[M3uEntry]) -> str:
    """Render an extended-M3U document (UTF-8) from ordered entries."""
    lines = ["#EXTM3U"]
    title = _one_line(name)
    if title:
        lines.append(f"#PLAYLIST:{title}")
    for entry in entries:
        seconds = -1
        if entry.duration is not None and entry.duration >= 0:
            seconds = int(entry.duration)
        display = f"{entry.artist} - {entry.title}" if entry.artist else entry.title
        lines.append(f"#EXTINF:{seconds},{_one_line(display)}")
        lines.append(entry.url)
    return "\n".join(lines) + "\n"


def download_filename(name: str) -> str:
    """Return a filesystem-safe ``.m3u`` filename for the container."""
    return f"{sanitize_member_name(name)}.m3u"


def content_disposition(filename: str) -> str:
    """Return a ``Content-Disposition`` attachment header value."""
    try:
        filename.encode("ascii")
        return f'attachment; filename="{filename}"'
    except UnicodeEncodeError:
        return f"attachment; filename*=UTF-8''{quote(filename)}"


async def build_embedder(
    session: AsyncSession,
    item_type: str,
    item_id: str,
    user: Optional[User],
    supplied_token: Optional[str],
    access: str,
) -> Embedder:
    """
    Build the lazy resolver for the share token embedded in track URLs.

    ``access="exclude"`` never embeds a token.  ``access="token"`` reuses a
    supplied token valid for the container, else mints a fresh share token
    for a requester with manage rights — deferred until a non-public track
    actually needs it, so exporting fully public content creates no row.
    Raises ``M3uExportError`` (403) when token mode is requested but no token
    can be provided.
    """
    if access != "token":

        async def _none() -> Optional[str]:
            return None

        return _none

    if supplied_token is not None and await sharing.validate_share_token(session, item_type, item_id, supplied_token):

        async def _supplied() -> Optional[str]:
            return supplied_token

        return _supplied

    if user is None or not await acl.can_manage(session, user, item_type, item_id):
        raise M3uExportError(
            "Embedding a share token requires a valid share token for the " "container or manage rights on it",
            status_code=403,
        )

    minted: Optional[str] = None

    async def _mint() -> Optional[str]:
        nonlocal minted
        if minted is None:
            _row, minted = await sharing.create_share_token(session, item_type, item_id, user.id)
        return minted

    return _mint


async def _track_playable(session: AsyncSession, track: Track, policy_cache: dict) -> bool:
    """Return whether an anonymous consumer can be served the track's media.

    File-backed tracks always qualify; provider-backed tracks must have an
    active external reference and a stream policy that permits anonymous
    playback (share tokens cannot satisfy provider policies — they carry no
    user identity).
    """
    if track.audio_file_id is not None:
        return True
    external_ref = getattr(track, "external_track", None) or getattr(track, "external_item", None)
    if external_ref is None or external_ref.state != "active":
        return False
    external_library = getattr(external_ref, "external_library", None)
    if external_library is None or not external_library.enabled:
        return False
    return await streaming.external_stream_allowed(session, external_library, None, cache=policy_cache)


async def _track_entry(
    session: AsyncSession,
    track: Track,
    base_url: str,
    *,
    container_public: bool,
    embed: Optional[Embedder],
    policy_cache: dict,
) -> Optional[M3uEntry]:
    """Build a stream entry, embedding a share token when needed/possible."""
    if not await _track_playable(session, track, policy_cache):
        return None
    # Sale-gated tracks keep the stream URL — the endpoint enforces the
    # sale's unpaid policy itself — except ``none``, which would only 403.
    from .payments import access as payment_access

    gate_cache = policy_cache.setdefault("sale_gates", {})
    track_access = await payment_access.gated_access(session, track, None, gate_cache=gate_cache)
    if track_access is not None and track_access.level == payment_access.ACCESS_NONE:
        return None
    url = f"{base_url}/api/v1/stream/{track.id}"
    # Member tracks of a public container are anonymously playable through
    # the ACL's derived-access rules (track → album/playlist), so they keep
    # clean URLs; other non-public tracks need an embedded share token —
    # unless another public container already derives anonymous access.
    if (
        track.visibility != Visibility.PUBLIC.value
        and not container_public
        and not await acl.can_access(session, None, "track", str(track.id))
    ):
        token = await embed() if embed is not None else None
        if token is None:
            return None
        url = f"{url}?token={token}"
    artist = track.artist.name if track.artist else ""
    return M3uEntry(url=url, title=track.title, artist=artist, duration=track.duration)


def _episode_entry(episode: PodcastEpisode) -> Optional[M3uEntry]:
    """Build an episode entry pointing at the remote enclosure URL."""
    if not episode.audio_url:
        return None
    podcast = episode.podcast
    artist = ""
    if podcast is not None:
        artist = podcast.title or podcast.author or ""
    return M3uEntry(
        url=episode.audio_url,
        title=episode.title,
        artist=artist,
        duration=float(episode.duration_seconds) if episode.duration_seconds is not None else None,
    )


def _remote_playable(row: RemoteObject, rendition_map: dict) -> bool:
    """Mirror ``resolve_media_url`` against a batched rendition lookup."""
    if row.unavailable_at is not None:
        return False
    if row.audio_url:
        return True
    if row.object_type in _REMOTE_MEDIA_TYPES or row.resource_type == "track":
        return row.canonical_url in rendition_map
    return False


def _remote_entry(row: RemoteObject, base_url: str) -> M3uEntry:
    """Build a remote entry pointing at the play-time resolving redirect."""
    return M3uEntry(
        url=f"{base_url}{remote_content.remote_object_stream_url(row)}",
        title=row.name or row.canonical_url,
    )


async def _track_entries(
    session: AsyncSession,
    tracks: List[Track],
    base_url: str,
    *,
    container_public: bool,
    embed: Optional[Embedder],
) -> Tuple[List[M3uEntry], int]:
    """Build entries for local tracks, counting dropped ones."""
    from .payments import access as payment_access

    entries: List[M3uEntry] = []
    skipped = 0
    policy_cache: dict = {"sale_gates": {}}
    await payment_access.prewarm_gate_cache(session, tracks, policy_cache["sale_gates"])
    for track in tracks:
        entry = await _track_entry(
            session, track, base_url, container_public=container_public, embed=embed, policy_cache=policy_cache
        )
        if entry is None:
            skipped += 1
        else:
            entries.append(entry)
    return entries, skipped


async def album_document(
    session: AsyncSession,
    album: Album,
    user: Optional[User],
    base_url: str,
    *,
    embed: Optional[Embedder] = None,
    limit: int = M3U_MAX_ENTRIES,
) -> Tuple[str, int]:
    """Render an album's accessible tracks as an M3U document.

    Returns ``(document, skipped)`` where ``skipped`` counts member tracks
    dropped because they are not playable by an anonymous consumer.
    """
    tracks, _ = await music.list_tracks(
        session,
        album_id=str(album.id),
        user=user,
        limit=limit,
        include={"artist"},
    )
    entries, skipped = await _track_entries(
        session,
        tracks,
        base_url,
        container_public=album.visibility == Visibility.PUBLIC.value,
        embed=embed,
    )
    return render_document(album.title, entries), skipped


async def playlist_document(
    session: AsyncSession,
    playlist: Playlist,
    user: Optional[User],
    config: SonghiveConfig,
    base_url: str,
    *,
    embed: Optional[Embedder] = None,
    limit: int = M3U_MAX_ENTRIES,
) -> Tuple[str, int]:
    """Render a playlist's accessible items as an M3U document.

    Local tracks resolve to instance stream URLs, podcast episodes keep
    their remote enclosure URL, and public remote objects resolve through
    the ``/api/v1/remote/objects/{id}/stream`` redirect. Returns
    ``(document, skipped)``.
    """
    rows = await music.list_playlist_items(
        session,
        str(playlist.id),
        user=user,
        limit=limit,
        include={"artist"},
    )

    container_public = playlist.visibility == Visibility.PUBLIC.value
    remote_allowed = remote_content.remote_search_policy(config) == "public"
    remote_rows = [
        row.remote_object
        for row in rows
        if row.remote_object is not None
        and row.remote_object.visibility == "public"
        and row.remote_object.unavailable_at is None
        and remote_allowed
        and remote_content.remote_domain_allowed(row.remote_object.domain, config)
    ]
    rendition_map = await remote_content.rendition_audio_map(
        session,
        [row.canonical_url for row in remote_rows],
    )
    remote_playable = {row.id for row in remote_rows if _remote_playable(row, rendition_map)}

    entries: List[M3uEntry] = []
    skipped = 0
    policy_cache: dict = {"sale_gates": {}}
    from .payments import access as payment_access

    await payment_access.prewarm_gate_cache(
        session,
        [row.track for row in rows if row.track is not None],
        policy_cache["sale_gates"],
    )
    for row in rows:
        entry: Optional[M3uEntry]
        if row.track is not None:
            entry = await _track_entry(
                session,
                row.track,
                base_url,
                container_public=container_public,
                embed=embed,
                policy_cache=policy_cache,
            )
        elif row.episode is not None:
            entry = _episode_entry(row.episode)
        elif row.remote_object is not None:
            remote = row.remote_object
            entry = _remote_entry(remote, base_url) if remote.id in remote_playable else None
        else:
            entry = None
        if entry is None:
            skipped += 1
        else:
            entries.append(entry)
    return render_document(playlist.name, entries), skipped
