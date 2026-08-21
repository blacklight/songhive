"""
Tests for the music importer.
"""

import pytest

from songhive.models.album import Album
from songhive.models.artist import Artist
from songhive.models.library import Library
from songhive.models.track import Track
from songhive.music.importer import import_file
from songhive.services.metadata import AudioMetadata
from songhive.storage.local import LocalStorage


@pytest.fixture
def local_storage(tmp_path):
    """Create a LocalStorage backend backed by a temp directory."""
    return LocalStorage(tmp_path / "media")


@pytest.fixture
def fake_metadata():
    """Return an AudioMetadata factory for a known track/album."""

    def _make(title="Test Song", artist="Test Artist", album="Test Album"):
        return AudioMetadata(
            title=title,
            artist=artist,
            album=album,
            mimetype="audio/mpeg",
        )

    return _make


@pytest.mark.asyncio
async def test_import_file_sets_owner_on_track_and_new_album(
    db_session, regular_user, tmp_path, local_storage, fake_metadata, monkeypatch
):
    """import_file sets owner_id on the created track and a new album."""
    monkeypatch.setattr(
        "songhive.services.import_.extract_metadata",
        lambda _: fake_metadata(),
    )

    library = Library(name="Test Library", owner_id=str(regular_user.id))
    db_session.add(library)
    await db_session.flush()

    file_path = tmp_path / "song.mp3"
    file_path.write_bytes(b"fake audio")

    upload = await import_file(
        db_session,
        file_path,
        str(library.id),
        local_storage,
        "local",
        owner_id=str(regular_user.id),
    )

    track = await db_session.get(Track, upload.track_id)
    assert track is not None
    assert track.owner_id == str(regular_user.id)
    assert track.album_id is not None

    album = await db_session.get(Album, track.album_id)
    assert album is not None
    assert album.owner_id == str(regular_user.id)


@pytest.mark.asyncio
async def test_import_file_does_not_overwrite_existing_album_owner(
    db_session, regular_user, tmp_path, local_storage, fake_metadata, monkeypatch
):
    """import_file does not change the owner of an existing album."""
    monkeypatch.setattr(
        "songhive.services.import_.extract_metadata",
        lambda _: fake_metadata(album="Shared Album"),
    )

    artist = Artist(name="Test Artist")
    db_session.add(artist)
    await db_session.flush()

    other_owner = "00000000-0000-0000-0000-000000000000"
    existing_album = Album(title="Shared Album", artist_id=artist.id, owner_id=other_owner)
    db_session.add(existing_album)
    await db_session.flush()

    library = Library(name="Test Library", owner_id=str(regular_user.id))
    db_session.add(library)
    await db_session.flush()

    file_path = tmp_path / "song.mp3"
    file_path.write_bytes(b"fake audio")

    upload = await import_file(
        db_session,
        file_path,
        str(library.id),
        local_storage,
        "local",
        owner_id=str(regular_user.id),
    )

    track = await db_session.get(Track, upload.track_id)
    assert track is not None
    assert track.owner_id == str(regular_user.id)
    assert track.album_id == existing_album.id

    album = await db_session.get(Album, track.album_id)
    assert album.owner_id == other_owner


@pytest.mark.asyncio
async def test_import_file_defaults_to_ownerless(
    db_session, regular_user, tmp_path, local_storage, fake_metadata, monkeypatch
):
    """import_file without an owner_id creates ownerless tracks and albums."""
    monkeypatch.setattr(
        "songhive.services.import_.extract_metadata",
        lambda _: fake_metadata(),
    )

    library = Library(name="Test Library", owner_id=str(regular_user.id))
    db_session.add(library)
    await db_session.flush()

    file_path = tmp_path / "song.mp3"
    file_path.write_bytes(b"fake audio")

    upload = await import_file(
        db_session,
        file_path,
        str(library.id),
        local_storage,
        "local",
    )

    track = await db_session.get(Track, upload.track_id)
    assert track is not None
    assert track.owner_id is None
    assert track.album_id is not None

    album = await db_session.get(Album, track.album_id)
    assert album is not None
    assert album.owner_id is None
