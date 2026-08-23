"""
Tests for the music importer.
"""

import hashlib
import pathlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from songhive.config.schema import StorageConfig
from songhive.models._enums import Visibility
from songhive.models.album import Album
from songhive.models.artist import Artist
from songhive.models.library import Library
from songhive.models.library_track import LibraryTrack
from songhive.models.stored_file import StoredFile
from songhive.models.track import Track
from songhive.models.user import User
from songhive.music.importer import import_file
from songhive.services.import_ import DuplicateTrackError, import_audio_file
from songhive.services.metadata import AudioMetadata
from songhive.services.storage import StorageService
from songhive.storage.local import LocalStorage
from songhive.tasks.import_ import process_upload, scan_directory


@pytest.fixture
def local_storage(tmp_path):
    """Create a LocalStorage backend backed by a temp directory."""
    return LocalStorage(tmp_path / "media")


@pytest.fixture
def local_storage_service(tmp_path):
    """Create a StorageService backed by a local temp directory."""
    backend = LocalStorage(tmp_path / "media")
    config = StorageConfig(backend="local", local_path=tmp_path / "media")
    return StorageService(backend, config)


@pytest.fixture
def fake_metadata():
    """Return an AudioMetadata factory for a known track/album."""

    def _make(
        title="Test Song",
        artist="Test Artist",
        album="Test Album",
        cover_art=None,
        cover_art_mime=None,
        duration=None,
        year=None,
    ):
        return AudioMetadata(
            title=title,
            artist=artist,
            album=album,
            mimetype="audio/mpeg",
            cover_art=cover_art,
            cover_art_mime=cover_art_mime,
            duration=duration,
            year=year,
        )

    return _make


# ---------------------------------------------------------------------------
# songhive.music.importer / songhive.services.import_
# ---------------------------------------------------------------------------


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


@pytest.mark.asyncio
async def test_import_file_stores_embedded_cover_art(
    db_session, regular_user, tmp_path, local_storage, fake_metadata, monkeypatch
):
    """import_file stores embedded cover art as a new stored file on the album."""
    cover = b"\xff\xd8\xff\xe0fake"
    monkeypatch.setattr(
        "songhive.services.import_.extract_metadata",
        lambda _: fake_metadata(cover_art=cover, cover_art_mime="image/jpeg"),
    )

    library = Library(name="Cover Library", owner_id=str(regular_user.id))
    db_session.add(library)
    await db_session.flush()

    file_path = tmp_path / "cover_song.mp3"
    file_path.write_bytes(b"fake audio with cover")

    upload = await import_file(
        db_session,
        file_path,
        str(library.id),
        local_storage,
        "local",
        owner_id=str(regular_user.id),
    )

    track = await db_session.get(Track, upload.track_id)
    album = await db_session.get(Album, track.album_id)
    assert album is not None
    assert album.cover_file_id is not None

    cover_file = await db_session.get(StoredFile, album.cover_file_id)
    assert cover_file is not None
    assert cover_file.content_type == "image/jpeg"
    assert cover_file.storage_path.startswith("covers/")


@pytest.mark.asyncio
async def test_import_file_raises_duplicate_by_bytes_for_owner(
    db_session, regular_user, tmp_path, local_storage, fake_metadata, monkeypatch
):
    """import_file raises DuplicateTrackError when the same file is already owned."""
    monkeypatch.setattr(
        "songhive.services.import_.extract_metadata",
        lambda _: fake_metadata(),
    )

    content = b"same audio bytes"
    sha = hashlib.sha256(content).hexdigest()
    stored = StoredFile(
        storage_path=f"files/{sha[:2]}/{sha[2:4]}/{sha}",
        storage_backend="local",
        content_type="audio/mpeg",
        size=len(content),
        sha256=sha,
    )
    db_session.add(stored)
    await db_session.flush()

    artist = Artist(name="Existing Artist")
    db_session.add(artist)
    await db_session.flush()

    track = Track(
        title="Existing",
        artist_id=artist.id,
        audio_file_id=str(stored.id),
        owner_id=str(regular_user.id),
        visibility=Visibility.PRIVATE.value,
        audio_mime_type="audio/mpeg",
    )
    db_session.add(track)
    await db_session.flush()

    library = Library(name="Dup Library", owner_id=str(regular_user.id))
    db_session.add(library)
    await db_session.flush()

    file_path = tmp_path / "same.mp3"
    file_path.write_bytes(content)

    with pytest.raises(DuplicateTrackError) as exc_info:
        await import_file(
            db_session,
            file_path,
            str(library.id),
            local_storage,
            "local",
            owner_id=str(regular_user.id),
        )

    assert exc_info.value.existing_track_id == str(track.id)


@pytest.mark.asyncio
async def test_import_file_raises_duplicate_by_bytes_for_public_track(
    db_session, tmp_path, local_storage, fake_metadata, monkeypatch
):
    """import_file raises DuplicateTrackError for an ownerless public duplicate."""
    monkeypatch.setattr(
        "songhive.services.import_.extract_metadata",
        lambda _: fake_metadata(),
    )

    content = b"public audio"
    sha = hashlib.sha256(content).hexdigest()
    stored = StoredFile(
        storage_path=f"files/{sha[:2]}/{sha[2:4]}/{sha}",
        storage_backend="local",
        content_type="audio/mpeg",
        size=len(content),
        sha256=sha,
    )
    db_session.add(stored)
    await db_session.flush()

    artist = Artist(name="Artist")
    db_session.add(artist)
    await db_session.flush()

    track = Track(
        title="Public",
        artist_id=artist.id,
        audio_file_id=str(stored.id),
        visibility=Visibility.PUBLIC.value,
        audio_mime_type="audio/mpeg",
    )
    db_session.add(track)
    await db_session.flush()

    library = Library(name="Lib", owner_id="00000000-0000-0000-0000-000000000000")
    db_session.add(library)
    await db_session.flush()

    file_path = tmp_path / "public.mp3"
    file_path.write_bytes(content)

    with pytest.raises(DuplicateTrackError) as exc_info:
        await import_file(
            db_session,
            file_path,
            str(library.id),
            local_storage,
            "local",
        )

    assert exc_info.value.existing_track_id == str(track.id)


@pytest.mark.asyncio
async def test_import_file_raises_duplicate_by_metadata(
    db_session, regular_user, tmp_path, local_storage, fake_metadata, monkeypatch
):
    """import_file raises DuplicateTrackError for a metadata duplicate in the same library."""
    metadata = fake_metadata(duration=120.0)
    monkeypatch.setattr(
        "songhive.services.import_.extract_metadata",
        lambda _: metadata,
    )

    artist = Artist(name="Test Artist")
    db_session.add(artist)
    await db_session.flush()

    album = Album(title="Test Album", artist_id=artist.id)
    db_session.add(album)
    await db_session.flush()

    library = Library(name="Meta Dup", owner_id=str(regular_user.id))
    db_session.add(library)
    await db_session.flush()

    stored = StoredFile(
        storage_path="files/a/b/c",
        storage_backend="local",
        content_type="audio/mpeg",
        size=1,
        sha256="a" * 64,
    )
    db_session.add(stored)
    await db_session.flush()

    track = Track(
        title="Test Song",
        artist_id=artist.id,
        album_id=album.id,
        duration=119.0,
        audio_file_id=str(stored.id),
        visibility=Visibility.PRIVATE.value,
        audio_mime_type="audio/mpeg",
    )
    db_session.add(track)
    await db_session.flush()

    library_track = LibraryTrack(library_id=str(library.id), track_id=str(track.id))
    db_session.add(library_track)
    await db_session.flush()

    file_path = tmp_path / "meta.mp3"
    file_path.write_bytes(b"unique audio for metadata duplicate")

    with pytest.raises(DuplicateTrackError) as exc_info:
        await import_file(
            db_session,
            file_path,
            str(library.id),
            local_storage,
            "local",
            owner_id=str(regular_user.id),
        )

    assert exc_info.value.existing_track_id == str(track.id)


@pytest.mark.asyncio
async def test_import_file_does_not_duplicate_when_duration_differs(
    db_session, regular_user, tmp_path, local_storage, fake_metadata, monkeypatch
):
    """import_file creates a new track when the duration does not match."""
    metadata = fake_metadata(duration=120.0)
    monkeypatch.setattr(
        "songhive.services.import_.extract_metadata",
        lambda _: metadata,
    )

    artist = Artist(name="Test Artist")
    db_session.add(artist)
    await db_session.flush()

    album = Album(title="Test Album", artist_id=artist.id)
    db_session.add(album)
    await db_session.flush()

    library = Library(name="Duration Filter", owner_id=str(regular_user.id))
    db_session.add(library)
    await db_session.flush()

    stored = StoredFile(
        storage_path="files/a/b/c",
        storage_backend="local",
        content_type="audio/mpeg",
        size=1,
        sha256="a" * 64,
    )
    db_session.add(stored)
    await db_session.flush()

    track = Track(
        title="Test Song",
        artist_id=artist.id,
        album_id=album.id,
        duration=90.0,
        audio_file_id=str(stored.id),
        visibility=Visibility.PRIVATE.value,
        audio_mime_type="audio/mpeg",
    )
    db_session.add(track)
    await db_session.flush()

    library_track = LibraryTrack(library_id=str(library.id), track_id=str(track.id))
    db_session.add(library_track)
    await db_session.flush()

    file_path = tmp_path / "notdup.mp3"
    file_path.write_bytes(b"other audio")

    upload = await import_file(
        db_session,
        file_path,
        str(library.id),
        local_storage,
        "local",
        owner_id=str(regular_user.id),
    )

    new_track = await db_session.get(Track, upload.track_id)
    assert new_track is not None
    assert str(new_track.id) != str(track.id)


@pytest.mark.asyncio
async def test_import_file_imports_track_when_title_missing(
    db_session, regular_user, tmp_path, local_storage, fake_metadata, monkeypatch
):
    """import_file falls back to the filename stem when no title is present."""
    metadata = fake_metadata(title=None)
    monkeypatch.setattr(
        "songhive.services.import_.extract_metadata",
        lambda _: metadata,
    )

    library = Library(name="No Title Library", owner_id=str(regular_user.id))
    db_session.add(library)
    await db_session.flush()

    file_path = tmp_path / "unknown.mp3"
    file_path.write_bytes(b"no title audio")

    upload = await import_file(
        db_session,
        file_path,
        str(library.id),
        local_storage,
        "local",
        owner_id=str(regular_user.id),
    )

    track = await db_session.get(Track, upload.track_id)
    assert track.title == "unknown"


@pytest.mark.asyncio
async def test_import_audio_file_raises_when_retrieve_fails(
    db_session, regular_user, tmp_path, local_storage_service, fake_metadata, monkeypatch
):
    """_extract_metadata_from_file raises when the backend cannot retrieve the file."""
    monkeypatch.setattr(
        "songhive.services.import_.extract_metadata",
        lambda _: fake_metadata(),
    )
    monkeypatch.setattr(
        local_storage_service.backend,
        "retrieve",
        AsyncMock(return_value=None),
    )

    library = Library(name="Retrieve Library", owner_id=str(regular_user.id))
    db_session.add(library)
    await db_session.flush()

    file_path = tmp_path / "missing.mp3"
    file_path.write_bytes(b"fake audio")

    with open(file_path, "rb") as f, pytest.raises(RuntimeError, match="Could not retrieve stored file"):
        await import_audio_file(
            db_session,
            storage_service=local_storage_service,
            file=f,
            filename="missing.mp3",
            library_id=str(library.id),
            owner_id=str(regular_user.id),
            enrich=False,
        )


@pytest.mark.asyncio
async def test_import_audio_file_removes_temporary_file_and_handles_is_relative_to_error(
    db_session, regular_user, tmp_path, local_storage, fake_metadata, monkeypatch
):
    """_extract_metadata_from_file removes temp files and survives is_relative_to errors."""
    monkeypatch.setattr(
        "songhive.services.import_.extract_metadata",
        lambda _: fake_metadata(),
    )

    outside = tmp_path / "outside"
    outside.mkdir()
    temp_path = outside / "song.mp3"
    temp_path.write_bytes(b"temp audio")

    original_is_relative_to = pathlib.PurePath.is_relative_to
    resolved_temp = temp_path.resolve()

    def fake_is_relative_to(self, other):
        if str(self) == str(resolved_temp):
            raise ValueError("cross-device")
        return original_is_relative_to(self, other)

    monkeypatch.setattr(pathlib.PurePath, "is_relative_to", fake_is_relative_to)

    storage_service = StorageService(
        local_storage,
        StorageConfig(backend="local", local_path=tmp_path / "media"),
    )
    monkeypatch.setattr(
        storage_service.backend,
        "retrieve",
        AsyncMock(return_value=temp_path),
    )

    library = Library(name="Temp Library", owner_id=str(regular_user.id))
    db_session.add(library)
    await db_session.flush()

    file_path = tmp_path / "input.mp3"
    file_path.write_bytes(b"input audio")

    with open(file_path, "rb") as f:
        result = await import_audio_file(
            db_session,
            storage_service=storage_service,
            file=f,
            filename="input.mp3",
            library_id=str(library.id),
            owner_id=str(regular_user.id),
            enrich=False,
        )

    assert result.track is not None
    assert not temp_path.exists()


@pytest.mark.asyncio
async def test_import_audio_file_swallows_enrichment_errors(
    db_session, regular_user, tmp_path, local_storage, fake_metadata, monkeypatch
):
    """import_audio_file does not fail when MusicBrainz enrichment cannot be queued."""
    monkeypatch.setattr(
        "songhive.services.import_.extract_metadata",
        lambda _: fake_metadata(),
    )

    boom = Mock()
    boom.delay = Mock(side_effect=RuntimeError("no broker"))
    monkeypatch.setattr("songhive.tasks.musicbrainz.enrich_track", boom)

    library = Library(name="Enrich Library", owner_id=str(regular_user.id))
    db_session.add(library)
    await db_session.flush()

    file_path = tmp_path / "enrich.mp3"
    file_path.write_bytes(b"fake")

    with open(file_path, "rb") as f:
        result = await import_audio_file(
            db_session,
            storage_service=StorageService(
                local_storage,
                StorageConfig(backend="local", local_path=tmp_path / "media"),
            ),
            file=f,
            filename="enrich.mp3",
            library_id=str(library.id),
            owner_id=str(regular_user.id),
            enrich=True,
        )

    assert result.track is not None
    boom.delay.assert_called_once()


# ---------------------------------------------------------------------------
# songhive.tasks.import_ helpers
# ---------------------------------------------------------------------------


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one(self):
        return self._value


class _FakeSession:
    def __init__(self, gets, exec_result=None):
        self._gets = gets
        self._exec_result = exec_result

    async def get(self, model, id):
        return self._gets.get((model, id))

    async def execute(self, stmt):
        return self._exec_result

    async def commit(self):
        pass


class _FakeSessionContext:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *args):
        return None


def _make_process_config(tmp_path, scan_roots=None):
    return SimpleNamespace(
        database=SimpleNamespace(url=f"sqlite+aiosqlite:///{tmp_path}/test.db"),
        storage=SimpleNamespace(backend="local", local_path=tmp_path / "media"),
        federation=SimpleNamespace(enabled=False, instance_domain=""),
        imports=SimpleNamespace(scan_roots=scan_roots or []),
    )


def _make_import_result(track, upload):
    return SimpleNamespace(
        track=track,
        upload=upload,
        library_track=SimpleNamespace(id="lt-1"),
        stored_file=SimpleNamespace(id="sf-1"),
        was_duplicate=False,
    )


# ---------------------------------------------------------------------------
# songhive.tasks.import_.process_upload
# ---------------------------------------------------------------------------


@pytest.fixture
def _patch_process_upload_env(monkeypatch, tmp_path):
    """Patch process_upload dependencies so no external services are needed."""
    dummy = tmp_path / "dummy.mp3"
    dummy.write_bytes(b"dummy")

    storage_backend = Mock()
    storage_backend.retrieve = AsyncMock(return_value=str(dummy))

    monkeypatch.setattr("songhive.config.load_config", lambda *_: _make_process_config(tmp_path))
    monkeypatch.setattr("songhive.models.base.init_db", Mock())
    monkeypatch.setattr("songhive.storage.get_storage", lambda cfg: storage_backend)
    monkeypatch.setattr("songhive.tasks.import_.publish_track_activity", Mock())

    return storage_backend


def test_process_upload_requires_exactly_one_source(monkeypatch, tmp_path):
    """process_upload raises when neither or both sources are provided."""
    monkeypatch.setattr("songhive.config.load_config", lambda *_: _make_process_config(tmp_path))
    monkeypatch.setattr("songhive.models.base.init_db", Mock())

    with pytest.raises(ValueError, match="exactly one of stored_file_id or file_path"):
        process_upload("lib-1")

    with pytest.raises(ValueError, match="exactly one of stored_file_id or file_path"):
        process_upload("lib-1", stored_file_id="sf-1", file_path="/some/file.mp3")


def test_process_upload_with_stored_file(_patch_process_upload_env, monkeypatch, tmp_path):
    """process_upload imports from a stored file and returns the upload id."""
    stored_file = SimpleNamespace(id="sf-1", storage_path="files/a/b/c", original_filename="stored.mp3")
    user = SimpleNamespace(id="user-1")
    track = SimpleNamespace(id="track-1", visibility=Visibility.PRIVATE.value, artist=None)
    upload = SimpleNamespace(id="upload-1")
    result = _make_import_result(track, upload)

    session = _FakeSession({(StoredFile, "sf-1"): stored_file, (User, "user-1"): user})
    monkeypatch.setattr(
        "songhive.models.base.get_session",
        lambda: _FakeSessionContext(session),
    )
    import_mock = AsyncMock(return_value=result)
    monkeypatch.setattr("songhive.services.import_.import_audio_file", import_mock)
    ws_mock = Mock()
    monkeypatch.setattr("songhive.ws.events.EventWebSocket", ws_mock)

    return_id = process_upload("lib-1", "user-1", stored_file_id="sf-1")

    assert return_id == "upload-1"
    import_mock.assert_awaited_once()
    ws_mock.broadcast.assert_called_once()


def test_process_upload_raises_when_stored_file_not_found(_patch_process_upload_env, monkeypatch, tmp_path):
    """process_upload raises when the referenced StoredFile does not exist."""
    session = _FakeSession({})
    monkeypatch.setattr(
        "songhive.models.base.get_session",
        lambda: _FakeSessionContext(session),
    )
    with pytest.raises(ValueError, match="StoredFile sf-1 not found"):
        process_upload("lib-1", stored_file_id="sf-1")


def test_process_upload_raises_when_backend_retrieve_fails(_patch_process_upload_env, monkeypatch, tmp_path):
    """process_upload raises when the backend cannot retrieve the stored file."""
    storage_backend = _patch_process_upload_env
    storage_backend.retrieve = AsyncMock(return_value=None)

    stored_file = SimpleNamespace(id="sf-1", storage_path="files/a/b/c", original_filename="stored.mp3")
    session = _FakeSession({(StoredFile, "sf-1"): stored_file})
    monkeypatch.setattr(
        "songhive.models.base.get_session",
        lambda: _FakeSessionContext(session),
    )
    with pytest.raises(ValueError, match="Could not retrieve stored file: files/a/b/c"):
        process_upload("lib-1", stored_file_id="sf-1")


def test_process_upload_with_file_path(_patch_process_upload_env, monkeypatch, tmp_path):
    """process_upload imports from a filesystem path and returns the upload id."""
    file_path = tmp_path / "local_song.mp3"
    file_path.write_bytes(b"local audio")
    track = SimpleNamespace(id="track-1", visibility=Visibility.PRIVATE.value, artist=None)
    upload = SimpleNamespace(id="upload-1")
    result = _make_import_result(track, upload)

    session = _FakeSession({(User, "user-1"): SimpleNamespace(id="user-1")})
    monkeypatch.setattr(
        "songhive.models.base.get_session",
        lambda: _FakeSessionContext(session),
    )
    import_mock = AsyncMock(return_value=result)
    monkeypatch.setattr("songhive.services.import_.import_audio_file", import_mock)
    ws_mock = Mock()
    monkeypatch.setattr("songhive.ws.events.EventWebSocket", ws_mock)

    return_id = process_upload("lib-1", "user-1", file_path=str(file_path), filename="override.mp3")

    assert return_id == "upload-1"
    call_kwargs = import_mock.call_args.kwargs
    assert call_kwargs["filename"] == "override.mp3"
    ws_mock.broadcast.assert_called_once()


def test_process_upload_publishes_public_track(_patch_process_upload_env, monkeypatch, tmp_path):
    """process_upload publishes a federation activity when the track is public."""
    stored_file = SimpleNamespace(id="sf-1", storage_path="files/a/b/c", original_filename="stored.mp3")
    user = SimpleNamespace(id="user-1")
    track = SimpleNamespace(
        id="track-1",
        visibility=Visibility.PUBLIC.value,
        artist=SimpleNamespace(name="Artist"),
    )
    upload = SimpleNamespace(id="upload-1")
    result = _make_import_result(track, upload)

    session = _FakeSession(
        {(StoredFile, "sf-1"): stored_file, (User, "user-1"): user},
        exec_result=_FakeResult(track),
    )
    monkeypatch.setattr(
        "songhive.models.base.get_session",
        lambda: _FakeSessionContext(session),
    )
    import_mock = AsyncMock(return_value=result)
    monkeypatch.setattr("songhive.services.import_.import_audio_file", import_mock)
    ws_mock = Mock()
    monkeypatch.setattr("songhive.ws.events.EventWebSocket", ws_mock)
    publish_mock = Mock()
    monkeypatch.setattr("songhive.tasks.import_.publish_track_activity", publish_mock)

    return_id = process_upload("lib-1", "user-1", stored_file_id="sf-1", visibility="public")

    assert return_id == "upload-1"
    assert track.federation_object_id is not None
    publish_mock.assert_called_once()
    assert publish_mock.call_args.args[0] is track


def test_process_upload_handles_duplicate(_patch_process_upload_env, monkeypatch, tmp_path):
    """process_upload returns the existing track id on duplicate detection."""
    stored_file = SimpleNamespace(id="sf-1", storage_path="files/a/b/c", original_filename="stored.mp3")
    session = _FakeSession({(StoredFile, "sf-1"): stored_file})
    monkeypatch.setattr(
        "songhive.models.base.get_session",
        lambda: _FakeSessionContext(session),
    )

    import_mock = AsyncMock(side_effect=DuplicateTrackError("existing-track"))
    monkeypatch.setattr("songhive.services.import_.import_audio_file", import_mock)
    ws_mock = Mock()
    monkeypatch.setattr("songhive.ws.events.EventWebSocket", ws_mock)

    return_id = process_upload("lib-1", stored_file_id="sf-1")

    assert return_id == "existing-track"
    ws_mock.broadcast.assert_called_once()
    assert ws_mock.broadcast.call_args.args[0] == "import.duplicate"


# ---------------------------------------------------------------------------
# songhive.tasks.import_.scan_directory
# ---------------------------------------------------------------------------


def test_scan_directory_outside_allowed_roots(monkeypatch, tmp_path):
    """scan_directory raises when the path is outside configured scan roots."""
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    fake_config = _make_process_config(tmp_path, scan_roots=[str(allowed)])
    monkeypatch.setattr("songhive.config.load_config", lambda *_: fake_config)

    with pytest.raises(ValueError, match="outside configured scan roots"):
        scan_directory(str(outside), "lib-1")


def test_scan_directory_raises_when_not_configured(monkeypatch, tmp_path):
    """scan_directory raises when no scan roots are configured."""
    fake_config = _make_process_config(tmp_path, scan_roots=[])
    monkeypatch.setattr("songhive.config.load_config", lambda *_: fake_config)

    with pytest.raises(ValueError, match="directory scanning is not configured"):
        scan_directory(str(tmp_path / "music"), "lib-1")


def test_scan_directory_enqueues_audio_files(monkeypatch, tmp_path):
    """scan_directory enqueues process_upload for supported audio files."""
    music_dir = tmp_path / "music"
    music_dir.mkdir()
    (music_dir / "song1.mp3").write_bytes(b"song1")
    (music_dir / "song2.flac").write_bytes(b"song2")
    (music_dir / "readme.txt").write_text("not audio")

    fake_config = _make_process_config(tmp_path, scan_roots=[str(tmp_path)])
    monkeypatch.setattr("songhive.config.load_config", lambda *_: fake_config)

    delay_mock = Mock()
    monkeypatch.setattr(process_upload, "delay", delay_mock)

    count = scan_directory(str(music_dir), "lib-1", "user-1")

    assert count == 2
    assert delay_mock.call_count == 2
    for call in delay_mock.call_args_list:
        assert call.kwargs.get("source") == "import"
        assert "file_path" in call.kwargs
