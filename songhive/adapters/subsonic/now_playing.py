"""
In-memory "now playing" registry for ``getNowPlaying.view``.

Subsonic clients expect the server to know what is currently playing across
users. Songhive broadcasts now-playing events over WebSockets but does not
persist them, so the adapter keeps a small process-local registry populated
by ``scrobble.view?submission=false``. Stream/download requests do not
register here: clients prefetch and cache audio they never play.

Entries expire after ``TTL``; because the store is in-memory it is local to
the serving process — under multi-process deployments ``getNowPlaying``
reflects only the process that answered the request.
"""

import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

#: How long a now-playing entry stays visible (10 minutes).
TTL_SECONDS = 600.0


@dataclass
class NowPlayingEntry:
    """A single in-flight play event."""

    track_id: str
    username: str
    player: Optional[str]
    started_at: float


_ENTRIES: Dict[Tuple[str, str], NowPlayingEntry] = {}


def record(user_id: str, username: str, track_id: str, player: Optional[str] = None) -> None:
    """Record that ``user_id`` started playing ``track_id``."""
    _ENTRIES[(str(user_id), str(track_id))] = NowPlayingEntry(
        track_id=str(track_id),
        username=username,
        player=player,
        started_at=time.time(),
    )


def current() -> List[NowPlayingEntry]:
    """Return all unexpired now-playing entries, most recent first."""
    cutoff = time.time() - TTL_SECONDS
    stale = [key for key, entry in _ENTRIES.items() if entry.started_at < cutoff]
    for key in stale:
        del _ENTRIES[key]
    return sorted(_ENTRIES.values(), key=lambda entry: entry.started_at, reverse=True)


def minutes_ago(entry: NowPlayingEntry) -> int:
    """Return whole minutes since the entry started playing."""
    return max(0, int((time.time() - entry.started_at) // 60))


def _clear() -> None:
    """Drop all entries (test helper)."""
    _ENTRIES.clear()
