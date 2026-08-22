"""
Tests for the transcoding Celery task.
"""

import io

import pytest
from sqlalchemy import select

from songhive.config.schema import SonghiveConfig, StorageConfig
from songhive.models._enums import Visibility
from songhive.models.artist import Artist
from songhive.models.base import init_db
from songhive.models.library import Library
from songhive.models.track import Track
from songhive.models.transcoded_file import TranscodedFile
from songhive.models.upload import Upload
from songhive.services.storage import StorageService
from songhive.storage import get_storage
from songhive.streaming.transcoder import Transcoder, TranscoderError, TranscodeResult
from songhive.tasks.transcoding import _transcode_upload


@pytest.mark.asyncio
async def test_transcode_upload_caches_output(monkeypatch, db_session, engine, regular_user, tmp_path):
    """_transcode_upload stores a cached transcode for an upload."""
    init_db(engine=engine, force=True)

    media_path = tmp_path / "media"
    media_path.mkdir()
    storage_config = StorageConfig(backend="local", local_path=media_path)
    storage_service = StorageService(get_storage(storage_config), storage_config)

    config = SonghiveConfig(
        auth={"secret_key": "a" * 64},  # type: ignore
        database={"url": str(engine.url)},  # type: ignore
        storage={"backend": "local", "local_path": str(media_path)},  # type: ignore
    )
    monkeypatch.setattr("songhive.tasks.transcoding.load_config", lambda *_: config)

    source = io.BytesIO(b"audio data")
    stored = await storage_service.store_file(
        db_session,
        source,
        "audio/mpeg",
        owner_id=str(regular_user.id),
    )
    db_session.add(stored)

    artist = Artist(name="Artist")
    library = Library(name="Library", owner_id=str(regular_user.id))
    db_session.add_all([artist, library])
    await db_session.flush()

    track = Track(
        title="Track",
        artist_id=artist.id,
        audio_file_id=stored.id,
        owner_id=str(regular_user.id),
        visibility=Visibility.PUBLIC.value,
    )
    db_session.add(track)
    await db_session.flush()

    upload = Upload(
        track_id=track.id,
        library_id=library.id,
        storage_path=stored.storage_path,
        storage_backend="local",
        mimetype="audio/mpeg",
        stored_file_id=stored.id,
    )
    db_session.add(upload)
    await db_session.commit()

    async def _fake_transcode(input_path, output_format, output_dir=None, *_, **__):
        ext = Transcoder.FORMAT_MAP[output_format]["ext"]
        mimetype = Transcoder.FORMAT_MAP[output_format]["mimetype"]
        out = (output_dir or input_path.parent) / f"{input_path.stem}.{ext}"
        out.write_bytes(b"transcoded " + output_format.encode())
        return TranscodeResult(output_path=out, mimetype=mimetype)

    monkeypatch.setattr(
        "songhive.tasks.transcoding.Transcoder.transcode",
        staticmethod(_fake_transcode),
    )

    file_id = await _transcode_upload(upload.id, "opus", "128k")
    assert file_id

    row = await db_session.scalar(select(TranscodedFile).where(TranscodedFile.track_id == track.id))
    assert row is not None
    assert row.format == "opus"
    assert row.bitrate == "128k"
    assert row.stored_file is not None


@pytest.mark.asyncio
async def test_transcode_upload_missing_upload(monkeypatch, db_session, engine, tmp_path):
    """_transcode_upload returns an empty string for a missing upload."""
    init_db(engine=engine, force=True)

    media_path = tmp_path / "media"
    media_path.mkdir()
    config = SonghiveConfig(
        auth={"secret_key": "a" * 64},  # type: ignore
        database={"url": str(engine.url)},  # type: ignore
        storage={"backend": "local", "local_path": str(media_path)},  # type: ignore
    )
    monkeypatch.setattr("songhive.tasks.transcoding.load_config", lambda *_: config)

    file_id = await _transcode_upload("nonexistent", "opus", "128k")
    assert file_id == ""


@pytest.mark.asyncio
async def test_transcode_upload_unsupported_format(monkeypatch, db_session, engine, regular_user, tmp_path):
    """_transcode_upload raises TranscoderError for an unsupported target format."""

    media_path = tmp_path / "media"
    media_path.mkdir()
    storage_config = StorageConfig(backend="local", local_path=media_path)
    storage_service = StorageService(get_storage(storage_config), storage_config)

    source = io.BytesIO(b"audio data")
    stored = await storage_service.store_file(
        db_session,
        source,
        "audio/mpeg",
        owner_id=str(regular_user.id),
    )
    db_session.add(stored)

    artist = Artist(name="Artist")
    library = Library(name="Library", owner_id=str(regular_user.id))
    db_session.add_all([artist, library])
    await db_session.flush()

    track = Track(
        title="Track",
        artist_id=artist.id,
        audio_file_id=stored.id,
        owner_id=str(regular_user.id),
        visibility=Visibility.PUBLIC.value,
    )
    db_session.add(track)
    await db_session.flush()

    upload = Upload(
        track_id=track.id,
        library_id=library.id,
        storage_path=stored.storage_path,
        storage_backend="local",
        mimetype="audio/mpeg",
        stored_file_id=stored.id,
    )
    db_session.add(upload)
    await db_session.commit()

    config = SonghiveConfig(
        auth={"secret_key": "a" * 64},  # type: ignore
        database={"url": str(engine.url)},  # type: ignore
        storage={"backend": "local", "local_path": str(media_path)},  # type: ignore
    )
    monkeypatch.setattr("songhive.tasks.transcoding.load_config", lambda *_: config)

    with pytest.raises(TranscoderError, match="Unsupported format"):
        await _transcode_upload(upload.id, "wav", "128k")
