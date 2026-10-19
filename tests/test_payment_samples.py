"""
Tests for sample derivative handling (services/payments/samples.py).
"""

import asyncio
import hashlib
import secrets
from typing import Optional
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select

from songhive.models._enums import Visibility
from songhive.models.artist import Artist
from songhive.models.notification import Notification
from songhive.models.payments import Sale, SampleDerivative
from songhive.models.stored_file import StoredFile
from songhive.models.track import Track
from songhive.models.user import User
from songhive.services.payments import samples
from songhive.services.payments.errors import PaymentError
from songhive.services.storage import StorageService
from songhive.storage import get_storage


async def _make_artist(session) -> Artist:
    artist = Artist(name="Sample Artist")
    session.add(artist)
    await session.flush()
    return artist


async def _make_file(session, owner: Optional[User] = None, content: Optional[bytes] = None) -> StoredFile:
    seed = content if content is not None else secrets.token_bytes(16)
    sha = hashlib.sha256(seed).hexdigest()
    stored_file = StoredFile(
        storage_path=f"files/{sha[:2]}/{sha[2:4]}/{sha}",
        storage_backend="local",
        content_type="audio/mpeg",
        size=len(seed),
        sha256=sha,
        owner_id=owner.id if owner is not None else None,
        visibility=Visibility.PUBLIC.value,
    )
    session.add(stored_file)
    await session.flush()
    return stored_file


async def _make_track(
    session,
    owner: User,
    *,
    duration: Optional[float] = 200.0,
    with_file: bool = True,
) -> Track:
    artist = await _make_artist(session)
    audio_file = await _make_file(session, owner) if with_file else None
    track = Track(
        title="Sample Track",
        artist_id=artist.id,
        owner_id=owner.id,
        visibility=Visibility.PUBLIC.value,
        audio_file_id=audio_file.id if audio_file else None,
        duration=duration,
    )
    session.add(track)
    await session.flush()
    return track


def _make_sale(track: Track, owner: User, **overrides) -> Sale:
    params = {
        "owner_id": owner.id,
        "entity_type": "track",
        "track_id": track.id,
        "status": "draft",
        "price_minor": 500,
        "currency": "usd",
        "unpaid_policy": "sample",
        "sample_render_policy": "materialized",
    }
    params.update(overrides)
    return Sale(**params)


class TestSampleWindow:
    def test_defaults_from_config(self, config):
        sale = MagicMock(sample_start_seconds=0, sample_length_seconds=None)
        track = MagicMock(duration=200.0)
        assert samples.sample_window(sale, track, config) == (0, config.payments.default_sample_seconds)

    def test_custom_window(self, config):
        sale = MagicMock(sample_start_seconds=10, sample_length_seconds=20)
        track = MagicMock(duration=200.0)
        assert samples.sample_window(sale, track, config) == (10, 20)

    def test_length_clamped_to_max(self, config):
        sale = MagicMock(sample_start_seconds=0, sample_length_seconds=9999)
        track = MagicMock(duration=200.0)
        assert samples.sample_window(sale, track, config) == (0, config.payments.sample_max_seconds)

    def test_start_beyond_duration_rewinds(self, config):
        sale = MagicMock(sample_start_seconds=220, sample_length_seconds=60)
        track = MagicMock(duration=200.0)
        # start >= duration → start = duration - length
        assert samples.sample_window(sale, track, config) == (140, 60)

    def test_length_clamped_to_remaining(self, config):
        sale = MagicMock(sample_start_seconds=190, sample_length_seconds=None)
        track = MagicMock(duration=200.0)
        assert samples.sample_window(sale, track, config) == (190, 10)

    def test_no_duration(self, config):
        sale = MagicMock(sample_start_seconds=0, sample_length_seconds=None)
        track = MagicMock(duration=None)
        assert samples.sample_window(sale, track, config) == (0, config.payments.default_sample_seconds)


class TestEnsureSample:
    async def test_creates_pending_derivative(self, db_session, regular_user, config, monkeypatch):
        track = await _make_track(db_session, regular_user)
        sale = _make_sale(track, regular_user)
        db_session.add(sale)
        await db_session.commit()

        derivative = await samples.ensure_sample(db_session, track, sale, config, enqueue=False)
        await db_session.commit()

        assert derivative.track_id == track.id
        assert derivative.status == "pending"
        assert derivative.start_seconds == 0
        assert derivative.length_seconds == config.payments.default_sample_seconds
        stored = await db_session.scalar(select(StoredFile).where(StoredFile.sha256 == derivative.source_sha256))
        assert stored is not None

        # Second call reuses the row.
        again = await samples.ensure_sample(db_session, track, sale, config, enqueue=False)
        assert again.id == derivative.id

    async def test_no_audio_raises_422(self, db_session, regular_user, config):
        track = await _make_track(db_session, regular_user, with_file=False)
        sale = _make_sale(track, regular_user)
        db_session.add(sale)
        await db_session.commit()

        with pytest.raises(PaymentError) as exc_info:
            await samples.ensure_sample(db_session, track, sale, config, enqueue=False)
        assert exc_info.value.status_code == 422

    async def test_enqueues_materialization(self, db_session, regular_user, config, _no_real_celery_broker):
        track = await _make_track(db_session, regular_user)
        sale = _make_sale(track, regular_user)
        db_session.add(sale)
        await db_session.commit()

        await samples.ensure_sample(db_session, track, sale, config, enqueue=True)
        assert _no_real_celery_broker.called


class TestGetReadySample:
    async def test_returns_ready_match(self, db_session, regular_user, config):
        track = await _make_track(db_session, regular_user)
        sale = _make_sale(track, regular_user)
        db_session.add(sale)
        await db_session.commit()

        derivative = await samples.ensure_sample(db_session, track, sale, config, enqueue=False)
        derivative.status = "ready"
        await db_session.commit()

        found = await samples.get_ready_sample(db_session, track, sale, config)
        assert found is not None and found.id == derivative.id

    async def test_pending_is_not_ready(self, db_session, regular_user, config):
        track = await _make_track(db_session, regular_user)
        sale = _make_sale(track, regular_user)
        db_session.add(sale)
        await db_session.commit()

        await samples.ensure_sample(db_session, track, sale, config, enqueue=False)
        await db_session.commit()
        assert await samples.get_ready_sample(db_session, track, sale, config) is None

    async def test_no_audio_returns_none(self, db_session, regular_user, config):
        track = await _make_track(db_session, regular_user, with_file=False)
        sale = _make_sale(track, regular_user)
        db_session.add(sale)
        await db_session.commit()
        assert await samples.get_ready_sample(db_session, track, sale, config) is None


class TestMarkStaleSamples:
    async def test_marks_mismatched_sha_failed(self, db_session, regular_user, config):
        track = await _make_track(db_session, regular_user)
        other = await _make_track(db_session, regular_user)
        sale = _make_sale(track, regular_user)
        db_session.add(sale)
        await db_session.commit()

        current = await samples.ensure_sample(db_session, track, sale, config, enqueue=False)
        stale = SampleDerivative(
            track_id=track.id,
            source_sha256="0" * 64,
            start_seconds=0,
            length_seconds=30,
            status="ready",
        )
        untouched_other_track = SampleDerivative(
            track_id=other.id,
            source_sha256="0" * 64,
            start_seconds=0,
            length_seconds=30,
            status="pending",
        )
        db_session.add_all([stale, untouched_other_track])
        await db_session.commit()

        count = await samples.mark_stale_samples(db_session, track)
        await db_session.commit()
        assert count == 1
        assert stale.status == "failed"
        assert current.status == "pending"
        assert untouched_other_track.status == "pending"


class TestMaterializeDerivative:
    def _storage(self, config):
        return StorageService(get_storage(config.storage), config.storage)

    def _fake_ffmpeg(self, monkeypatch):
        """Patch ``asyncio.create_subprocess_exec`` to emit the output file."""

        async def _exec(*cmd, **kwargs):
            output_path = cmd[-1]
            from pathlib import Path

            Path(output_path).write_bytes(b"fake-mp3-data")
            proc = MagicMock()
            proc.returncode = 0
            proc.communicate = AsyncMock(return_value=(b"", b""))
            return proc

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _exec)

    async def test_missing_track_fails_derivative(self, db_session, config):
        derivative = SampleDerivative(track_id="missing", source_sha256="a" * 64, start_seconds=0, length_seconds=10)
        db_session.add(derivative)
        await db_session.commit()

        ok = await samples._materialize_derivative(db_session, derivative, self._storage(config), config)
        assert ok is False
        assert derivative.status == "failed"

    async def test_sha_mismatch_fails(self, db_session, regular_user, config):
        track = await _make_track(db_session, regular_user)
        await db_session.commit()
        derivative = SampleDerivative(
            track_id=track.id,
            source_sha256="0" * 64,  # does not match the current audio sha
            start_seconds=0,
            length_seconds=10,
        )
        db_session.add(derivative)
        await db_session.commit()

        ok = await samples._materialize_derivative(db_session, derivative, self._storage(config), config)
        assert ok is False
        assert derivative.status == "failed"

    async def test_missing_source_path_fails(self, db_session, regular_user, config, monkeypatch):
        """A stored file the backend cannot resolve fails the derivative."""
        track = await _make_track(db_session, regular_user)
        await db_session.commit()
        stored = await db_session.get(StoredFile, track.audio_file_id)

        derivative = SampleDerivative(
            track_id=track.id,
            source_sha256=stored.sha256,
            start_seconds=0,
            length_seconds=10,
        )
        db_session.add(derivative)
        await db_session.commit()

        storage = self._storage(config)
        storage.backend.retrieve = AsyncMock(return_value=None)
        ok = await samples._materialize_derivative(db_session, derivative, storage, config)
        assert ok is False
        assert derivative.status == "failed"

    async def test_success_marks_ready_and_stores_file(self, db_session, regular_user, config, monkeypatch, tmp_path):
        self._fake_ffmpeg(monkeypatch)

        # Store a real file so the local backend can resolve it.
        storage = self._storage(config)
        import io

        source_bytes = b"fake audio bytes"
        stored = await storage.store_file(
            db_session,
            io.BytesIO(source_bytes),
            "audio/mpeg",
            owner_id=regular_user.id,
            visibility=Visibility.PRIVATE.value,
        )
        artist = await _make_artist(db_session)
        track = Track(
            title="T",
            artist_id=artist.id,
            owner_id=regular_user.id,
            audio_file_id=stored.id,
            duration=100.0,
        )
        db_session.add(track)
        await db_session.commit()

        derivative = SampleDerivative(
            track_id=track.id, source_sha256=stored.sha256, start_seconds=0, length_seconds=10
        )
        db_session.add(derivative)
        await db_session.commit()

        ok = await samples._materialize_derivative(db_session, derivative, storage, config)
        assert ok is True
        assert derivative.status == "ready"
        assert derivative.stored_file_id is not None
        sample_file = await db_session.get(StoredFile, derivative.stored_file_id)
        assert sample_file.sha256 == hashlib.sha256(b"fake-mp3-data").hexdigest()

    async def test_ffmpeg_failure_marks_failed(self, db_session, regular_user, config, monkeypatch):
        async def _exec(*cmd, **kwargs):
            proc = MagicMock()
            proc.returncode = 1
            proc.communicate = AsyncMock(return_value=(b"", b"boom"))
            return proc

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _exec)

        storage = self._storage(config)
        import io

        stored = await storage.store_file(
            db_session,
            io.BytesIO(b"audio"),
            "audio/mpeg",
            owner_id=regular_user.id,
        )
        artist = await _make_artist(db_session)
        track = Track(title="T", artist_id=artist.id, owner_id=regular_user.id, audio_file_id=stored.id)
        db_session.add(track)
        await db_session.commit()

        derivative = SampleDerivative(
            track_id=track.id, source_sha256=stored.sha256, start_seconds=0, length_seconds=10
        )
        db_session.add(derivative)
        await db_session.commit()

        ok = await samples._materialize_derivative(db_session, derivative, storage, config)
        assert ok is False
        assert derivative.status == "failed"


class TestMaterializeTrackSamples:
    async def test_builds_pending_and_activates(self, db_session, regular_user, config, monkeypatch):
        track = await _make_track(db_session, regular_user)
        sale = _make_sale(track, regular_user)
        db_session.add(sale)
        await db_session.commit()

        await samples.ensure_sample(db_session, track, sale, config, enqueue=False)
        await db_session.commit()

        async def _mat(session, deriv, storage, cfg):
            deriv.status = "ready"
            deriv.stored_file_id = None
            return True

        monkeypatch.setattr(samples, "_materialize_derivative", _mat)
        built = await samples.materialize_track_samples(db_session, track.id, MagicMock(), config)
        assert built == 1
        await db_session.commit()

        # Ready derivatives activate the draft sale + notify the seller.
        await db_session.refresh(sale)
        assert sale.status == "active"
        notif = (
            await db_session.execute(select(Notification).where(Notification.user_id == regular_user.id))
        ).scalar_one()
        assert notif.payload["event"] == "sale_activated"
        assert notif.payload["sale_id"] == sale.id

    async def test_album_sale_waits_for_all_tracks(self, db_session, regular_user, config, monkeypatch):
        from songhive.models.album import Album

        artist = await _make_artist(db_session)
        album = Album(title="Alb", artist_id=artist.id, owner_id=regular_user.id)
        db_session.add(album)
        await db_session.flush()

        t1 = await _make_track(db_session, regular_user)
        t1.album_id = album.id
        t2 = await _make_track(db_session, regular_user)
        t2.album_id = album.id
        sale = _make_sale(t1, regular_user, entity_type="album", track_id=None, album_id=album.id)
        db_session.add(sale)
        await db_session.commit()

        # Only t1 has a ready derivative — the album sale stays draft.
        stored = await db_session.get(StoredFile, t1.audio_file_id)
        ready = SampleDerivative(
            track_id=t1.id,
            source_sha256=stored.sha256,
            start_seconds=0,
            length_seconds=config.payments.default_sample_seconds,
            status="ready",
        )
        db_session.add(ready)
        await db_session.commit()

        pending = SampleDerivative(
            track_id=t1.id,
            source_sha256=stored.sha256,
            start_seconds=5,
            length_seconds=10,
            status="pending",
        )
        db_session.add(pending)
        await db_session.commit()

        async def _mat(session, deriv, storage, cfg):
            deriv.status = "ready"
            return True

        monkeypatch.setattr(samples, "_materialize_derivative", _mat)
        built = await samples.materialize_track_samples(db_session, t1.id, MagicMock(), config)
        assert built == 1
        await db_session.refresh(sale)
        assert sale.status == "draft"  # t2 still lacks a ready sample


class TestSampleStoredFile:
    async def test_returns_stored_file_when_ready(self, db_session, regular_user, config):
        track = await _make_track(db_session, regular_user)
        sale = _make_sale(track, regular_user)
        db_session.add(sale)
        await db_session.commit()

        derivative = await samples.ensure_sample(db_session, track, sale, config, enqueue=False)
        derivative.status = "ready"
        derivative.stored_file_id = track.audio_file_id  # any StoredFile id works
        await db_session.commit()

        found = await samples.sample_stored_file(db_session, track, sale, config)
        assert found is not None
        assert found.id == track.audio_file_id

    async def test_materialized_policy_enqueues_and_returns_none(
        self, db_session, regular_user, config, _no_real_celery_broker
    ):
        track = await _make_track(db_session, regular_user)
        sale = _make_sale(track, regular_user)
        db_session.add(sale)
        await db_session.commit()

        result = await samples.sample_stored_file(db_session, track, sale, config)
        assert result is None
        assert _no_real_celery_broker.called

    async def test_on_demand_renders_under_lock(self, db_session, regular_user, config, fake_redis, monkeypatch):
        track = await _make_track(db_session, regular_user)
        sale = _make_sale(track, regular_user, sample_render_policy="on_demand")
        db_session.add(sale)
        await db_session.commit()

        stored = await db_session.get(StoredFile, track.audio_file_id)

        async def _mat(session, track_id, storage, cfg):
            # Simulate a successful render.
            result = await session.execute(select(SampleDerivative).where(SampleDerivative.track_id == track_id))
            for deriv in result.scalars():
                deriv.status = "ready"
                deriv.stored_file_id = stored.id
            return 1

        monkeypatch.setattr(samples, "materialize_track_samples", _mat)

        found = await samples.sample_stored_file(db_session, track, sale, config, storage=MagicMock(), redis=fake_redis)
        assert found is not None
        assert found.id == stored.id
        # The lock was released.
        assert await fake_redis.get(samples._LOCK_KEY.format(track_id=track.id)) is None

    async def test_on_demand_skips_when_lock_held(self, db_session, regular_user, config, fake_redis, monkeypatch):
        track = await _make_track(db_session, regular_user)
        sale = _make_sale(track, regular_user, sample_render_policy="on_demand")
        db_session.add(sale)
        await db_session.commit()

        # Another worker holds the lock — no render is attempted.
        await samples.acquire_sample_lock(fake_redis, track.id, 60)
        mat = AsyncMock()
        monkeypatch.setattr(samples, "materialize_track_samples", mat)

        result = await samples.sample_stored_file(
            db_session, track, sale, config, storage=MagicMock(), redis=fake_redis
        )
        assert result is None
        mat.assert_not_awaited()


class TestSampleLock:
    async def test_acquire_release(self, fake_redis):
        assert await samples.acquire_sample_lock(fake_redis, "t1", 60) is True
        # Second acquire while held fails.
        assert await samples.acquire_sample_lock(fake_redis, "t1", 60) is False
        await samples.release_sample_lock(fake_redis, "t1")
        assert await samples.acquire_sample_lock(fake_redis, "t1", 60) is True

    async def test_release_swallows_errors(self, fake_redis):
        bad = MagicMock()
        bad.delete = AsyncMock(side_effect=RuntimeError("down"))
        await samples.release_sample_lock(bad, "t1")


class TestEnqueue:
    def test_enqueue_materialization(self, _no_real_celery_broker):
        assert samples.enqueue_sample_materialization("t1") is True
        assert _no_real_celery_broker.called

    def test_enqueue_failure_returns_false(self, monkeypatch):
        from songhive.tasks import payments as payment_tasks

        monkeypatch.setattr(
            payment_tasks.materialize_track_sample,
            "delay",
            MagicMock(side_effect=RuntimeError("broker down")),
        )
        assert samples.enqueue_sample_materialization("t1") is False
