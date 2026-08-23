"""Additional tests for the import service and Celery task."""

import io
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from songhive.config.schema import ImportConfig, SonghiveConfig, StorageConfig
from songhive.models._enums import Visibility
from songhive.models.album import Album
from songhive.models.artist import Artist
from songhive.models.library import Library
from songhive.models.track import Track
from songhive.services.import_ import (
    DuplicateTrackError,
    _extract_metadata_from_file,
    import_audio_file,
)
from songhive.services.metadata import AudioMetadata
from songhive.services.storage import StorageService
from songhive.storage import get_storage
from songhive.tasks.import_ import process_upload, scan_directory


@pytest.fixture
def local_storage_service(tmp_path):
    config = StorageConfig(backend="local", local_path=tmp_path / "media")
    backend = get_storage(config)
    return StorageService(backend, config)


@pytest.fixture
def metadata_factory():
    def _make(**overrides):
        defaults = {
            "title": "Test Song",
            "artist": "Test Artist",
            "album": "Test Album",
            "mimetype": "audio/mpeg",
            "duration": 120.0,
        }
        defaults.update(overrides)
        return AudioMetadata(**defaults)

    return _make


@pytest.fixture
async def library(db_session, regular_user):
    library = Library(name="Test Library", owner_id=str(regular_user.id))
    db_session.add(library)
    await db_session.flush()
    return library


@pytest.fixture
def task_config(tmp_path):
    return SonghiveConfig(
        auth={"secret_key": "a" * 64},
        database={"url": f"sqlite+aiosqlite:///{tmp_path / 'task.db'}"},
        storage={"backend": "local", "local_path": str(tmp_path / "media")},
    )


@pytest.fixture
def patch_enrich_track(monkeypatch):
    monkeypatch.setattr(
        "songhive.tasks.musicbrainz.enrich_track",
        MagicMock(),
    )


@pytest.mark.asyncio
async def test_import_audio_file_creates_track_and_upload(
    db_session,
    library,
    local_storage_service,
    metadata_factory,
    monkeypatch,
    patch_enrich_track,
):
    monkeypatch.setattr(
        "songhive.services.import_.extract_metadata",
        lambda path: metadata_factory(),
    )

    result = await import_audio_file(
        db_session,
        storage_service=local_storage_service,
        file=io.BytesIO(b"fake audio"),
        filename="song.mp3",
        library_id=str(library.id),
        owner_id=str(library.owner_id),
    )

    assert result.track.title == "Test Song"
    assert result.track.owner_id == str(library.owner_id)
    assert result.upload.library_id == str(library.id)
    assert result.library_track.track_id == str(result.track.id)
    assert result.was_duplicate is False

    artist = await db_session.get(Track, str(result.track.id))
    assert artist is not None


@pytest.mark.asyncio
async def test_import_audio_file_stores_cover_art(
    db_session,
    library,
    local_storage_service,
    metadata_factory,
    monkeypatch,
    patch_enrich_track,
):
    monkeypatch.setattr(
        "songhive.services.import_.extract_metadata",
        lambda path: metadata_factory(cover_art=b"fake cover", cover_art_mime="image/jpeg"),
    )

    result = await import_audio_file(
        db_session,
        storage_service=local_storage_service,
        file=io.BytesIO(b"fake audio with cover"),
        filename="song.mp3",
        library_id=str(library.id),
        owner_id=str(library.owner_id),
    )

    track = await db_session.get(Track, str(result.track.id))
    assert track.album_id is not None
    album = await db_session.get(Album, track.album_id)
    assert album.cover_file_id is not None


@pytest.mark.asyncio
async def test_import_audio_file_enrichment_error_ignored(
    db_session,
    library,
    local_storage_service,
    metadata_factory,
    monkeypatch,
):
    mock = MagicMock()
    mock.delay.side_effect = RuntimeError("enrich fail")
    monkeypatch.setattr("songhive.tasks.musicbrainz.enrich_track", mock)
    monkeypatch.setattr(
        "songhive.services.import_.extract_metadata",
        lambda path: metadata_factory(),
    )

    result = await import_audio_file(
        db_session,
        storage_service=local_storage_service,
        file=io.BytesIO(b"fake audio"),
        filename="song.mp3",
        library_id=str(library.id),
        owner_id=str(library.owner_id),
    )

    assert result.track is not None


@pytest.mark.asyncio
async def test_import_audio_file_duplicate_by_bytes(
    db_session,
    library,
    local_storage_service,
    metadata_factory,
    monkeypatch,
    patch_enrich_track,
):
    monkeypatch.setattr(
        "songhive.services.import_.extract_metadata",
        lambda path: metadata_factory(),
    )

    first = await import_audio_file(
        db_session,
        storage_service=local_storage_service,
        file=io.BytesIO(b"shared content"),
        filename="song.mp3",
        library_id=str(library.id),
        owner_id=str(library.owner_id),
    )

    with pytest.raises(DuplicateTrackError) as exc:
        await import_audio_file(
            db_session,
            storage_service=local_storage_service,
            file=io.BytesIO(b"shared content"),
            filename="song.mp3",
            library_id=str(library.id),
            owner_id=str(library.owner_id),
        )

    assert exc.value.existing_track_id == str(first.track.id)


@pytest.mark.asyncio
async def test_import_audio_file_duplicate_by_bytes_ownerless_public(
    db_session,
    library,
    local_storage_service,
    metadata_factory,
    monkeypatch,
    patch_enrich_track,
):
    monkeypatch.setattr(
        "songhive.services.import_.extract_metadata",
        lambda path: metadata_factory(),
    )

    first = await import_audio_file(
        db_session,
        storage_service=local_storage_service,
        file=io.BytesIO(b"public content"),
        filename="song.mp3",
        library_id=str(library.id),
        owner_id=None,
        visibility=Visibility.PUBLIC.value,
    )

    with pytest.raises(DuplicateTrackError) as exc:
        await import_audio_file(
            db_session,
            storage_service=local_storage_service,
            file=io.BytesIO(b"public content"),
            filename="song.mp3",
            library_id=str(library.id),
            owner_id=None,
        )

    assert exc.value.existing_track_id == str(first.track.id)


@pytest.mark.asyncio
async def test_import_audio_file_duplicate_by_metadata(
    db_session,
    library,
    local_storage_service,
    metadata_factory,
    monkeypatch,
    patch_enrich_track,
):
    monkeypatch.setattr(
        "songhive.services.import_.extract_metadata",
        lambda path: metadata_factory(),
    )

    first = await import_audio_file(
        db_session,
        storage_service=local_storage_service,
        file=io.BytesIO(b"content a"),
        filename="song.mp3",
        library_id=str(library.id),
    )

    with pytest.raises(DuplicateTrackError) as exc:
        await import_audio_file(
            db_session,
            storage_service=local_storage_service,
            file=io.BytesIO(b"content b"),
            filename="song.mp3",
            library_id=str(library.id),
        )

    assert exc.value.existing_track_id == str(first.track.id)


@pytest.mark.asyncio
async def test_import_audio_file_title_none(
    db_session,
    library,
    local_storage_service,
    metadata_factory,
    monkeypatch,
    patch_enrich_track,
):
    monkeypatch.setattr(
        "songhive.services.import_.extract_metadata",
        lambda path: metadata_factory(title=None, artist=None),
    )

    result = await import_audio_file(
        db_session,
        storage_service=local_storage_service,
        file=io.BytesIO(b"no metadata"),
        filename="unknown_song.mp3",
        library_id=str(library.id),
        owner_id=str(library.owner_id),
    )

    assert result.track.title == "unknown_song"
    artist = await db_session.get(Artist, result.track.artist_id)
    assert artist.name == "Unknown Artist"


@pytest.mark.asyncio
async def test_import_audio_file_reuses_existing_artist_and_album(
    db_session,
    library,
    local_storage_service,
    metadata_factory,
    monkeypatch,
    patch_enrich_track,
):
    monkeypatch.setattr(
        "songhive.services.import_.extract_metadata",
        lambda path: metadata_factory(),
    )

    first = await import_audio_file(
        db_session,
        storage_service=local_storage_service,
        file=io.BytesIO(b"first audio"),
        filename="song.mp3",
        library_id=str(library.id),
        owner_id=str(library.owner_id),
    )

    monkeypatch.setattr(
        "songhive.services.import_.extract_metadata",
        lambda path: metadata_factory(title="Second Song"),
    )

    second = await import_audio_file(
        db_session,
        storage_service=local_storage_service,
        file=io.BytesIO(b"second audio"),
        filename="song2.mp3",
        library_id=str(library.id),
        owner_id=str(library.owner_id),
    )

    assert second.track.artist_id == first.track.artist_id
    assert second.track.album_id == first.track.album_id


@pytest.mark.asyncio
async def test_import_audio_file_enrich_false_skips_musicbrainz(
    db_session,
    library,
    local_storage_service,
    metadata_factory,
    monkeypatch,
):
    monkeypatch.setattr(
        "songhive.services.import_.extract_metadata",
        lambda path: metadata_factory(),
    )

    result = await import_audio_file(
        db_session,
        storage_service=local_storage_service,
        file=io.BytesIO(b"fake audio"),
        filename="song.mp3",
        library_id=str(library.id),
        owner_id=str(library.owner_id),
        enrich=False,
    )

    assert result.track is not None


@pytest.mark.asyncio
async def test_extract_metadata_from_file_retrieve_missing_raises(local_storage_service):
    backend = MagicMock()
    backend.base_path = local_storage_service.backend.base_path
    backend.retrieve = AsyncMock(return_value=None)
    service = StorageService(backend, local_storage_service.config)

    with pytest.raises(RuntimeError, match="Could not retrieve stored file"):
        await _extract_metadata_from_file(service, MagicMock(storage_path="missing"))


@pytest.mark.asyncio
async def test_extract_metadata_from_file_removes_temp_file(tmp_path, local_storage_service, monkeypatch):
    temp_file = tmp_path / "temp.mp3"
    temp_file.write_bytes(b"fake audio")

    backend = MagicMock()
    backend.base_path = local_storage_service.backend.base_path
    backend.retrieve = AsyncMock(return_value=temp_file)
    service = StorageService(backend, local_storage_service.config)

    monkeypatch.setattr(
        "songhive.services.import_.extract_metadata",
        lambda path: AudioMetadata(),
    )

    await _extract_metadata_from_file(service, MagicMock(storage_path="files/temp"))
    assert not temp_file.exists()


@pytest.mark.asyncio
async def test_extract_metadata_from_file_ignores_relative_to_error(tmp_path, local_storage_service, monkeypatch):
    temp_file = tmp_path / "temp.mp3"
    temp_file.write_bytes(b"fake audio")

    backend = MagicMock()
    backend.base_path = local_storage_service.backend.base_path
    backend.retrieve = AsyncMock(return_value=temp_file)
    service = StorageService(backend, local_storage_service.config)

    monkeypatch.setattr(
        "songhive.services.import_.extract_metadata",
        lambda path: AudioMetadata(),
    )
    monkeypatch.setattr(
        "pathlib.PurePath.is_relative_to",
        lambda self, other: (_ for _ in ()).throw(ValueError("bad path")),
    )

    await _extract_metadata_from_file(service, MagicMock(storage_path="files/temp"))
    assert not temp_file.exists()


def _fake_session(session):
    @asynccontextmanager
    async def _cm():
        yield session

    return _cm


def test_process_upload_requires_exactly_one_source():
    with pytest.raises(ValueError, match="exactly one of"):
        process_upload.run("lib", file_path="/a", stored_file_id="b")

    with pytest.raises(ValueError, match="exactly one of"):
        process_upload.run("lib")


def test_process_upload_from_file_path(tmp_path, task_config, monkeypatch):
    song = tmp_path / "song.mp3"
    song.write_bytes(b"fake audio")

    mock_import = AsyncMock(
        return_value=MagicMock(
            track=MagicMock(visibility=Visibility.PRIVATE.value, id="track-1"),
            upload=MagicMock(id="upload-1"),
        )
    )
    mock_broadcast = MagicMock()

    monkeypatch.setattr("songhive.config.load_config", lambda *a, **k: task_config)
    monkeypatch.setattr("songhive.models.base.init_db", lambda *a, **k: None)
    monkeypatch.setattr("songhive.models.base.get_session", _fake_session(MagicMock()))
    monkeypatch.setattr("songhive.storage.get_storage", lambda *a, **k: MagicMock())
    monkeypatch.setattr("songhive.services.import_.import_audio_file", mock_import)
    monkeypatch.setattr("songhive.ws.events.EventWebSocket.broadcast", mock_broadcast)

    result = process_upload.run("lib", file_path=str(song))

    assert result == "upload-1"
    assert mock_import.called
    assert mock_broadcast.called


def test_process_upload_public_track_publishes_federation(tmp_path, task_config, monkeypatch):
    song = tmp_path / "song.mp3"
    song.write_bytes(b"fake audio")

    owner = MagicMock()
    fake_track = MagicMock()
    fake_track.artist = MagicMock()
    fake_track.federation_object_id = None

    fake_session = MagicMock()
    fake_session.get = AsyncMock(side_effect=lambda model, _id: owner)
    fake_session.execute = AsyncMock(return_value=MagicMock(scalar_one=MagicMock(return_value=fake_track)))
    fake_session.commit = AsyncMock()

    mock_import = AsyncMock(
        return_value=MagicMock(
            track=MagicMock(visibility=Visibility.PUBLIC.value, id="track-2"),
            upload=MagicMock(id="upload-2"),
        )
    )
    mock_publish = MagicMock()
    mock_broadcast = MagicMock()

    monkeypatch.setattr("songhive.config.load_config", lambda *a, **k: task_config)
    monkeypatch.setattr("songhive.models.base.init_db", lambda *a, **k: None)
    monkeypatch.setattr("songhive.models.base.get_session", _fake_session(fake_session))
    monkeypatch.setattr("songhive.storage.get_storage", lambda *a, **k: MagicMock())
    monkeypatch.setattr("songhive.services.import_.import_audio_file", mock_import)
    monkeypatch.setattr("songhive.tasks.import_.publish_track_activity", mock_publish)
    monkeypatch.setattr("songhive.ws.events.EventWebSocket.broadcast", mock_broadcast)

    result = process_upload.run("lib", "owner-1", file_path=str(song))

    assert result == "upload-2"
    assert fake_track.federation_object_id is not None
    assert mock_publish.called
    assert mock_broadcast.called


def test_process_upload_duplicate_broadcasts_import_duplicate(tmp_path, task_config, monkeypatch):
    song = tmp_path / "song.mp3"
    song.write_bytes(b"fake audio")

    mock_import = AsyncMock(side_effect=DuplicateTrackError("track-123"))
    mock_broadcast = MagicMock()

    monkeypatch.setattr("songhive.config.load_config", lambda *a, **k: task_config)
    monkeypatch.setattr("songhive.models.base.init_db", lambda *a, **k: None)
    monkeypatch.setattr("songhive.models.base.get_session", _fake_session(MagicMock()))
    monkeypatch.setattr("songhive.storage.get_storage", lambda *a, **k: MagicMock())
    monkeypatch.setattr("songhive.services.import_.import_audio_file", mock_import)
    monkeypatch.setattr("songhive.ws.events.EventWebSocket.broadcast", mock_broadcast)

    result = process_upload.run("lib", file_path=str(song))

    assert result == "track-123"
    assert mock_broadcast.called


def test_process_upload_from_stored_file(tmp_path, task_config, monkeypatch):
    song = tmp_path / "stored.mp3"
    song.write_bytes(b"fake audio")

    backend = MagicMock()
    backend.retrieve = AsyncMock(return_value=song)
    backend.base_path = tmp_path / "media"

    stored_file = MagicMock(original_filename="stored.mp3", storage_path="files/abc")
    fake_session = MagicMock()
    fake_session.get = AsyncMock(return_value=stored_file)

    mock_import = AsyncMock(
        return_value=MagicMock(
            track=MagicMock(visibility=Visibility.PRIVATE.value, id="track-3"),
            upload=MagicMock(id="upload-3"),
        )
    )
    mock_broadcast = MagicMock()

    monkeypatch.setattr("songhive.config.load_config", lambda *a, **k: task_config)
    monkeypatch.setattr("songhive.models.base.init_db", lambda *a, **k: None)
    monkeypatch.setattr("songhive.models.base.get_session", _fake_session(fake_session))
    monkeypatch.setattr("songhive.storage.get_storage", lambda *a, **k: backend)
    monkeypatch.setattr("songhive.services.import_.import_audio_file", mock_import)
    monkeypatch.setattr("songhive.ws.events.EventWebSocket.broadcast", mock_broadcast)

    result = process_upload.run("lib", stored_file_id="sf-1")

    assert result == "upload-3"
    assert mock_import.called
    assert mock_import.call_args.kwargs.get("filename") == "stored.mp3"


def test_process_upload_missing_stored_file(tmp_path, task_config, monkeypatch):
    fake_session = MagicMock()
    fake_session.get = AsyncMock(return_value=None)

    monkeypatch.setattr("songhive.config.load_config", lambda *a, **k: task_config)
    monkeypatch.setattr("songhive.models.base.init_db", lambda *a, **k: None)
    monkeypatch.setattr("songhive.models.base.get_session", _fake_session(fake_session))
    monkeypatch.setattr("songhive.storage.get_storage", lambda *a, **k: MagicMock())

    with pytest.raises(ValueError, match="StoredFile .* not found"):
        process_upload.run("lib", stored_file_id="missing")


def test_process_upload_missing_retrieved_path(tmp_path, task_config, monkeypatch):
    backend = MagicMock()
    backend.retrieve = AsyncMock(return_value=None)
    backend.base_path = tmp_path / "media"

    stored_file = MagicMock(original_filename="stored.mp3", storage_path="files/abc")
    fake_session = MagicMock()
    fake_session.get = AsyncMock(return_value=stored_file)

    monkeypatch.setattr("songhive.config.load_config", lambda *a, **k: task_config)
    monkeypatch.setattr("songhive.models.base.init_db", lambda *a, **k: None)
    monkeypatch.setattr("songhive.models.base.get_session", _fake_session(fake_session))
    monkeypatch.setattr("songhive.storage.get_storage", lambda *a, **k: backend)

    with pytest.raises(ValueError, match="Could not retrieve stored file"):
        process_upload.run("lib", stored_file_id="sf-1")


def test_scan_directory_enqueues_audio_files(tmp_path, task_config, monkeypatch):
    music_dir = tmp_path / "music"
    music_dir.mkdir()
    (music_dir / "song.mp3").write_bytes(b"fake")
    (music_dir / "cover.jpg").write_bytes(b"cover")

    config = task_config.model_copy(update={"imports": ImportConfig(scan_roots=[str(music_dir)])})
    mock_process = MagicMock()
    mock_process.delay = MagicMock()

    monkeypatch.setattr("songhive.config.load_config", lambda *a, **k: config)
    monkeypatch.setattr("songhive.tasks.import_.process_upload", mock_process)

    count = scan_directory.run(str(music_dir), "lib-1", "owner-1")

    assert count == 1
    mock_process.delay.assert_called_once_with(
        "lib-1", "owner-1", file_path=str(music_dir / "song.mp3"), source="import"
    )


def test_scan_directory_rejects_outside_root(tmp_path, task_config, monkeypatch):
    scan_dir = tmp_path / "scan"
    scan_dir.mkdir()
    other_dir = tmp_path / "other"
    other_dir.mkdir()

    config = task_config.model_copy(update={"imports": ImportConfig(scan_roots=[str(other_dir)])})
    monkeypatch.setattr("songhive.config.load_config", lambda *a, **k: config)

    with pytest.raises(ValueError, match="outside configured scan roots"):
        scan_directory.run(str(scan_dir), "lib-1")


def test_scan_directory_requires_scan_roots(tmp_path, task_config, monkeypatch):
    config = task_config.model_copy(update={"imports": ImportConfig(scan_roots=[])})
    monkeypatch.setattr("songhive.config.load_config", lambda *a, **k: config)

    with pytest.raises(ValueError, match="directory scanning is not configured"):
        scan_directory.run(str(tmp_path / "scan"), "lib-1")
