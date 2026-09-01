"""Tests for upload duplicate detection against external tracks."""

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import status
from sqlalchemy import select

from songhive.models.artist import Artist
from songhive.models.external_library import ExternalLibrary
from songhive.models.external_track import ExternalTrack
from songhive.models.library import Library
from songhive.models.library_track import LibraryTrack
from songhive.models.stored_file import StoredFile
from songhive.models.track import Track
from songhive.models.upload import Upload
from songhive.services.metadata import AudioMetadata

MATCHING_AUDIO_HASH = "a" * 64
OTHER_AUDIO_HASH = "b" * 64


def _audio_hash_for_content(path: Path) -> str:
    """Return a deterministic audio-only hash based on file contents."""
    with open(path, "rb") as f:
        data = f.read()
    if b"match" in data:
        return MATCHING_AUDIO_HASH
    return OTHER_AUDIO_HASH


async def _make_artist(db_session, name: str = "Test Artist") -> Artist:
    """Create and return an artist."""
    artist = Artist(name=name)
    db_session.add(artist)
    await db_session.flush()
    return artist


async def _create_external_track_for_user(
    db_session,
    user,
    sha256: str = MATCHING_AUDIO_HASH,
) -> tuple[Library, ExternalLibrary, Track, ExternalTrack]:
    """Create an external library with a single active track for the user."""
    library = Library(
        name="External Library",
        owner_id=str(user.id),
        visibility="private",
    )
    db_session.add(library)
    await db_session.flush()

    external_library = ExternalLibrary(
        library_id=str(library.id),
        provider_type="fake",
        name="Fake Library",
    )
    db_session.add(external_library)
    await db_session.flush()

    artist = await _make_artist(db_session)
    track = Track(
        title="External Song",
        artist_id=str(artist.id),
        source="external",
        owner_id=str(user.id),
        visibility="private",
    )
    db_session.add(track)
    await db_session.flush()

    library_track = LibraryTrack(
        library_id=str(library.id),
        track_id=str(track.id),
    )
    db_session.add(library_track)

    external_track = ExternalTrack(
        external_library_id=str(external_library.id),
        track_id=str(track.id),
        provider_key="song.mp3",
        sha256=sha256,
        state="active",
    )
    db_session.add(external_track)
    await db_session.flush()

    return library, external_library, track, external_track


@pytest.fixture(autouse=True)
def _patch_metadata_and_hash(monkeypatch):
    """Use stable, fast metadata and audio-only hashing in upload tests."""
    monkeypatch.setattr(
        "songhive.services.import_.extract_metadata",
        lambda _: AudioMetadata(
            title="Uploaded Song",
            artist="Uploaded Artist",
            album="Uploaded Album",
            mimetype="audio/mpeg",
            duration=123.0,
        ),
    )
    monkeypatch.setattr(
        "songhive.services.import_.audio_hash",
        AsyncMock(side_effect=_audio_hash_for_content),
    )


@pytest.mark.asyncio
async def test_upload_returns_409_when_matching_external_track(
    client,
    regular_user,
    auth_headers,
    db_session,
):
    """Uploading an audio file with a matching external hash returns a 409 token."""
    _, _, track, _ = await _create_external_track_for_user(db_session, regular_user)
    await db_session.commit()

    headers = auth_headers(regular_user)
    response = client.post(
        "/api/v1/files/upload",
        files={"file": ("match.mp3", b"match audio content", "audio/mpeg")},
        headers=headers,
    )

    assert response.status_code == status.HTTP_409_CONFLICT
    data = response.json()
    assert data["sha256"] == MATCHING_AUDIO_HASH
    assert data["provider_type"] == "fake"
    assert data["token"]
    assert len(data["display_info"]) == 1
    assert data["display_info"][0]["provider_type"] == "fake"
    assert data["display_info"][0]["external_library_id"]

    stored_files = list(
        (await db_session.execute(select(StoredFile).where(StoredFile.sha256 == MATCHING_AUDIO_HASH))).scalars().all()
    )
    assert len(stored_files) == 1


@pytest.mark.asyncio
async def test_upload_keep_local_creates_local_track(
    client,
    regular_user,
    auth_headers,
    db_session,
):
    """Uploading with external_duplicate_action=keep_local creates a local track."""
    _, _, track, _ = await _create_external_track_for_user(db_session, regular_user)
    await db_session.commit()

    headers = auth_headers(regular_user)
    response = client.post(
        "/api/v1/files/upload?external_duplicate_action=keep_local",
        files={"file": ("match.mp3", b"match audio content", "audio/mpeg")},
        headers=headers,
    )

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["content_type"] == "audio/mpeg"
    assert "X-Track-Id" in response.headers
    assert response.headers["X-Track-Id"] != str(track.id)

    track_id = response.headers["X-Track-Id"]
    track_result = await db_session.get(Track, track_id)
    assert track_result is not None
    assert track_result.audio_file_id is not None

    upload = await db_session.execute(select(Upload).where(Upload.track_id == track_id))
    assert upload.scalar_one_or_none() is not None


@pytest.mark.asyncio
async def test_upload_discard_upload_returns_external_track(
    client,
    regular_user,
    auth_headers,
    db_session,
):
    """Uploading with external_duplicate_action=discard_upload returns the external track."""
    _, _, track, _ = await _create_external_track_for_user(db_session, regular_user)
    await db_session.commit()

    headers = auth_headers(regular_user)
    response = client.post(
        "/api/v1/files/upload?external_duplicate_action=discard_upload",
        files={"file": ("match.mp3", b"match audio content", "audio/mpeg")},
        headers=headers,
    )

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["id"] == str(track.id)
    assert data["is_external"] is True

    stored_files = list(
        (await db_session.execute(select(StoredFile).where(StoredFile.sha256 == MATCHING_AUDIO_HASH))).scalars().all()
    )
    assert len(stored_files) == 0


@pytest.mark.asyncio
async def test_resolve_keep_local_creates_track(
    client,
    regular_user,
    auth_headers,
    db_session,
):
    """Resolving a token with keep_local finalizes the upload."""
    _, _, track, _ = await _create_external_track_for_user(db_session, regular_user)
    await db_session.commit()

    headers = auth_headers(regular_user)
    response = client.post(
        "/api/v1/files/upload",
        files={"file": ("match.mp3", b"match audio content", "audio/mpeg")},
        headers=headers,
    )
    assert response.status_code == status.HTTP_409_CONFLICT
    token = response.json()["token"]

    resolve = client.post(
        "/api/v1/files/upload/resolve-duplicate",
        json={"token": token, "action": "keep_local"},
        headers=headers,
    )
    assert resolve.status_code == status.HTTP_200_OK
    data = resolve.json()
    assert data["content_type"] == "audio/mpeg"
    assert "X-Track-Id" in resolve.headers
    assert resolve.headers["X-Track-Id"] != str(track.id)


@pytest.mark.asyncio
async def test_resolve_discard_upload_returns_external_track(
    client,
    regular_user,
    auth_headers,
    db_session,
):
    """Resolving a token with discard_upload removes the stored file and returns the external track."""
    _, _, track, _ = await _create_external_track_for_user(db_session, regular_user)
    await db_session.commit()

    headers = auth_headers(regular_user)
    response = client.post(
        "/api/v1/files/upload",
        files={"file": ("match.mp3", b"match audio content", "audio/mpeg")},
        headers=headers,
    )
    assert response.status_code == status.HTTP_409_CONFLICT
    token = response.json()["token"]

    stored_files = list(
        (await db_session.execute(select(StoredFile).where(StoredFile.sha256 == MATCHING_AUDIO_HASH))).scalars().all()
    )
    assert len(stored_files) == 1

    resolve = client.post(
        "/api/v1/files/upload/resolve-duplicate",
        json={"token": token, "action": "discard_upload"},
        headers=headers,
    )
    assert resolve.status_code == status.HTTP_200_OK
    data = resolve.json()
    assert data["id"] == str(track.id)
    assert data["is_external"] is True

    stored_files = list(
        (await db_session.execute(select(StoredFile).where(StoredFile.sha256 == MATCHING_AUDIO_HASH))).scalars().all()
    )
    assert len(stored_files) == 0


@pytest.mark.asyncio
async def test_resolve_expired_token_returns_404(
    client,
    regular_user,
    auth_headers,
    db_session,
):
    """Resolving an unknown or expired token returns 404."""
    await _create_external_track_for_user(db_session, regular_user)
    await db_session.commit()

    headers = auth_headers(regular_user)
    resolve = client.post(
        "/api/v1/files/upload/resolve-duplicate",
        json={"token": "nonexistent-token", "action": "keep_local"},
        headers=headers,
    )
    assert resolve.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
async def test_resolve_cross_user_token_rejected(
    client,
    regular_user,
    other_user,
    auth_headers,
    db_session,
):
    """A user cannot resolve a token that belongs to another user."""
    await _create_external_track_for_user(db_session, regular_user)
    await db_session.commit()

    headers = auth_headers(regular_user)
    response = client.post(
        "/api/v1/files/upload",
        files={"file": ("match.mp3", b"match audio content", "audio/mpeg")},
        headers=headers,
    )
    assert response.status_code == status.HTTP_409_CONFLICT
    token = response.json()["token"]

    other_headers = auth_headers(other_user)
    resolve = client.post(
        "/api/v1/files/upload/resolve-duplicate",
        json={"token": token, "action": "keep_local"},
        headers=other_headers,
    )
    assert resolve.status_code == status.HTTP_403_FORBIDDEN


async def _create_shadowed_only_external_track(
    db_session,
    user,
    sha256: str = MATCHING_AUDIO_HASH,
) -> tuple[Library, ExternalLibrary, ExternalTrack]:
    """Create an external track in shadowed state with no linked Songhive track."""
    library = Library(
        name="External Library",
        owner_id=str(user.id),
        visibility="private",
    )
    db_session.add(library)
    await db_session.flush()

    external_library = ExternalLibrary(
        library_id=str(library.id),
        provider_type="fake",
        name="Fake Library",
    )
    db_session.add(external_library)
    await db_session.flush()

    external_track = ExternalTrack(
        external_library_id=str(external_library.id),
        provider_key="song.mp3",
        sha256=sha256,
        state="shadowed",
        track_id=None,
    )
    db_session.add(external_track)
    await db_session.commit()

    return library, external_library, external_track


@pytest.mark.asyncio
async def test_upload_shadowed_only_does_not_crash(
    client,
    regular_user,
    auth_headers,
    db_session,
):
    """Uploading a file that only matches a shadowed, unlinked external track does not crash."""
    _, _, _ = await _create_shadowed_only_external_track(db_session, regular_user)

    headers = auth_headers(regular_user)
    response = client.post(
        "/api/v1/files/upload?external_duplicate_action=discard_upload",
        files={"file": ("match.mp3", b"match audio content", "audio/mpeg")},
        headers=headers,
    )

    assert response.status_code == status.HTTP_200_OK
    assert "X-Track-Id" in response.headers


@pytest.mark.asyncio
async def test_bulk_upload_marks_external_duplicate(
    client,
    regular_user,
    auth_headers,
    db_session,
):
    """Bulk upload completes non-matching files and marks the matching one."""
    await _create_external_track_for_user(db_session, regular_user)
    await db_session.commit()

    headers = auth_headers(regular_user)
    files = [
        ("files", ("other.mp3", b"other audio content", "audio/mpeg")),
        ("files", ("match.mp3", b"match audio content", "audio/mpeg")),
    ]
    response = client.post(
        "/api/v1/files/upload/bulk",
        files=files,
        headers=headers,
    )

    assert response.status_code == status.HTTP_200_OK
    results = response.json()
    assert len(results) == 2

    statuses = {r["filename"]: r.get("status") for r in results}
    assert statuses["other.mp3"] is None or statuses["other.mp3"] != "external_duplicate"
    assert statuses["match.mp3"] == "external_duplicate"

    match_result = next(r for r in results if r["filename"] == "match.mp3")
    assert match_result["external_duplicate"]
    assert match_result["external_duplicate"]["sha256"] == MATCHING_AUDIO_HASH

    other_result = next(r for r in results if r["filename"] == "other.mp3")
    assert other_result["stored_file"] is not None
