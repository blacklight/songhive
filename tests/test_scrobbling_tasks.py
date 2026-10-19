"""
Tests for the scrobbling Celery tasks (tasks/scrobbling.py).

``autoretry_for`` wraps ``task.run`` around the real bound task, so tests
patch ``task.retry``/``task.request.retries`` on the task itself and invoke
``task.run`` in a thread (the body calls ``asyncio.run``). Redis dedup goes
through the autouse fake sync Redis fixture; submissions are stubbed on
``services.scrobbler``.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from celery.exceptions import Retry

from songhive.config.schema import ScrobblingConfig
from songhive.models._enums import Visibility
from songhive.models.artist import Artist
from songhive.models.base import init_db
from songhive.models.scrobble import ScrobbleConfig
from songhive.models.track import Track
from songhive.services import scrobbler
from songhive.services.scrobbler import ScrobblerError
from songhive.tasks import scrobbling as scrobble_tasks


def _scrobbling_config(config, **overrides):
    params = {
        "enabled": True,
        "lastfm_api_key": "lastfm-key",
        "lastfm_api_secret": "lastfm-secret",
        "librefm_api_key": "librefm-key",
        "librefm_api_secret": "librefm-secret",
    }
    params.update(overrides)
    return config.model_copy(update={"scrobbling": ScrobblingConfig(**params)})


def _patch_config(monkeypatch, config):
    monkeypatch.setattr("songhive.tasks.scrobbling.load_config", lambda *_: config)


def _patch_retries(monkeypatch, task, retries: int = 0):
    """Make the bound task's retry raise a Retry exception at a given count."""
    monkeypatch.setattr(task, "retry", MagicMock(return_value=Retry("retrying")))
    monkeypatch.setattr(task.request, "retries", retries)


async def _run_task(task, *args):
    return await asyncio.to_thread(task.run, *args)


async def _make_track(db_session, owner) -> Track:
    artist = Artist(name="Scrobble Artist")
    db_session.add(artist)
    await db_session.flush()
    track = Track(
        title="Scrobble Track",
        artist_id=artist.id,
        owner_id=owner.id,
        visibility=Visibility.PUBLIC.value,
        duration=180.0,
    )
    db_session.add(track)
    await db_session.commit()
    return track


async def _make_scrobble_config(db_session, user, **overrides) -> ScrobbleConfig:
    params = {
        "user_id": user.id,
        "service": "lastfm",
        "username": "listener",
        "session_key": "encrypted-sk",
        "enabled": True,
    }
    params.update(overrides)
    row = ScrobbleConfig(**params)
    db_session.add(row)
    await db_session.commit()
    return row


class TestScrobbleNowPlaying:
    async def test_disabled_feature_returns_none(self, db_session, engine, config, monkeypatch):
        _patch_config(monkeypatch, _scrobbling_config(config, enabled=False))
        result = await _run_task(scrobble_tasks.scrobble_now_playing, "u", "t")
        assert result is None

    async def test_missing_user_retries_then_drops(self, db_session, engine, config, monkeypatch):
        init_db(engine=engine, force=True)
        _patch_config(monkeypatch, _scrobbling_config(config))

        task = scrobble_tasks.scrobble_now_playing
        _patch_retries(monkeypatch, task, retries=0)
        with pytest.raises(Retry):
            await _run_task(task, "no-user", "t1")
        task.retry.assert_called_once()
        assert task.retry.call_args.kwargs["countdown"] == 5

        _patch_retries(monkeypatch, task, retries=task.max_retries)
        result = await _run_task(task, "no-user", "t1")
        assert result is None

    async def test_missing_track_retries(self, db_session, engine, config, monkeypatch, regular_user):
        init_db(engine=engine, force=True)
        _patch_config(monkeypatch, _scrobbling_config(config))

        task = scrobble_tasks.scrobble_now_playing
        _patch_retries(monkeypatch, task, retries=0)
        with pytest.raises(Retry):
            await _run_task(task, regular_user.id, "no-track")
        task.retry.assert_called_once()

    async def test_no_scrobble_config_returns_disabled(self, db_session, engine, config, monkeypatch, regular_user):
        init_db(engine=engine, force=True)
        _patch_config(monkeypatch, _scrobbling_config(config))
        track = await _make_track(db_session, regular_user)

        result = await _run_task(scrobble_tasks.scrobble_now_playing, regular_user.id, track.id)
        assert result == "disabled"

    async def test_unavailable_service_returns_disabled(self, db_session, engine, config, monkeypatch, regular_user):
        """A config for a service with no API keys cannot submit."""
        init_db(engine=engine, force=True)
        # No lastfm credentials configured.
        _patch_config(
            monkeypatch,
            config.model_copy(update={"scrobbling": ScrobblingConfig(enabled=True)}),
        )
        track = await _make_track(db_session, regular_user)
        await _make_scrobble_config(db_session, regular_user)

        result = await _run_task(scrobble_tasks.scrobble_now_playing, regular_user.id, track.id)
        assert result == "disabled"

    async def test_disabled_row_returns_disabled(self, db_session, engine, config, monkeypatch, regular_user):
        init_db(engine=engine, force=True)
        _patch_config(monkeypatch, _scrobbling_config(config))
        track = await _make_track(db_session, regular_user)
        await _make_scrobble_config(db_session, regular_user, enabled=False)

        result = await _run_task(scrobble_tasks.scrobble_now_playing, regular_user.id, track.id)
        assert result == "disabled"

    async def test_submit_success_marks_dedup(self, db_session, engine, config, monkeypatch, regular_user):
        init_db(engine=engine, force=True)
        cfg = _scrobbling_config(config)
        _patch_config(monkeypatch, cfg)
        track = await _make_track(db_session, regular_user)
        await _make_scrobble_config(db_session, regular_user)
        submit = AsyncMock()
        monkeypatch.setattr(scrobbler, "submit_now_playing", submit)

        result = await _run_task(scrobble_tasks.scrobble_now_playing, regular_user.id, track.id)
        assert result == "done"
        submit.assert_awaited_once()
        fields = submit.await_args.args[2]
        assert fields["track"] == "Scrobble Track"
        assert fields["artist"] == "Scrobble Artist"

        # The dedup marker suppresses an immediate repeat.
        result = await _run_task(scrobble_tasks.scrobble_now_playing, regular_user.id, track.id)
        assert result == "duplicate"
        submit.assert_awaited_once()

    async def test_submit_error_propagates(self, db_session, engine, config, monkeypatch, regular_user):
        init_db(engine=engine, force=True)
        _patch_config(monkeypatch, _scrobbling_config(config))
        track = await _make_track(db_session, regular_user)
        await _make_scrobble_config(db_session, regular_user)
        monkeypatch.setattr(scrobbler, "submit_now_playing", AsyncMock(side_effect=ScrobblerError("rejected")))

        with pytest.raises(ScrobblerError):
            await _run_task(scrobble_tasks.scrobble_now_playing, regular_user.id, track.id)

    async def test_remote_entity_kind_dedup_key(self, db_session, engine, config, monkeypatch, regular_user):
        """Remote scrobbles dedup on a key that includes the entity kind."""
        init_db(engine=engine, force=True)
        cfg = _scrobbling_config(config)
        _patch_config(monkeypatch, cfg)
        await _make_scrobble_config(db_session, regular_user)
        monkeypatch.setattr(
            scrobble_tasks,
            "_load_scrobble_fields",
            AsyncMock(return_value={"artist": "a", "track": "t"}),
        )
        monkeypatch.setattr(scrobbler, "submit_now_playing", AsyncMock())

        key = scrobble_tasks._dedup_key("np", regular_user.id, "remote", "ro-1")
        scrobble_tasks._mark_submitted(cfg, key, 60)
        result = await _run_task(scrobble_tasks.scrobble_now_playing, regular_user.id, "ro-1", "remote")
        assert result == "duplicate"


class TestScrobbleTrack:
    async def test_disabled_feature_returns_none(self, db_session, engine, config, monkeypatch):
        _patch_config(monkeypatch, _scrobbling_config(config, enabled=False))
        result = await _run_task(scrobble_tasks.scrobble_track, "u", "t", 123)
        assert result is None

    async def test_missing_track_drops_after_max_retries(self, db_session, engine, config, monkeypatch, regular_user):
        init_db(engine=engine, force=True)
        _patch_config(monkeypatch, _scrobbling_config(config))

        task = scrobble_tasks.scrobble_track
        _patch_retries(monkeypatch, task, retries=task.max_retries)
        result = await _run_task(task, regular_user.id, "no-track", 123)
        assert result is None

    async def test_no_config_returns_disabled(self, db_session, engine, config, monkeypatch, regular_user):
        init_db(engine=engine, force=True)
        _patch_config(monkeypatch, _scrobbling_config(config))
        track = await _make_track(db_session, regular_user)

        result = await _run_task(scrobble_tasks.scrobble_track, regular_user.id, track.id, 123)
        assert result == "disabled"

    async def test_submit_success(self, db_session, engine, config, monkeypatch, regular_user):
        init_db(engine=engine, force=True)
        _patch_config(monkeypatch, _scrobbling_config(config))
        track = await _make_track(db_session, regular_user)
        await _make_scrobble_config(db_session, regular_user)
        submit = AsyncMock()
        monkeypatch.setattr(scrobbler, "submit_scrobble", submit)

        result = await _run_task(scrobble_tasks.scrobble_track, regular_user.id, track.id, 1_700_000_000)
        assert result == "done"
        submit.assert_awaited_once()
        assert submit.await_args.args[3] == 1_700_000_000

        # The same play scrobbled twice within the dedup window is dropped.
        result = await _run_task(scrobble_tasks.scrobble_track, regular_user.id, track.id, 1_700_000_000)
        assert result == "duplicate"
        submit.assert_awaited_once()

    async def test_submit_error_propagates(self, db_session, engine, config, monkeypatch, regular_user):
        init_db(engine=engine, force=True)
        _patch_config(monkeypatch, _scrobbling_config(config))
        track = await _make_track(db_session, regular_user)
        await _make_scrobble_config(db_session, regular_user)
        monkeypatch.setattr(scrobbler, "submit_scrobble", AsyncMock(side_effect=ScrobblerError("bad sig")))

        with pytest.raises(ScrobblerError):
            await _run_task(scrobble_tasks.scrobble_track, regular_user.id, track.id, 1_700_000_000)


class TestDedupHelpers:
    def test_dedup_key(self):
        assert scrobble_tasks._dedup_key("np", "u1", "t1") == "songhive:scrobble:np:u1:t1"

    def test_recently_submitted_fails_open(self, config, monkeypatch):
        """A Redis outage must not block submissions."""
        monkeypatch.setattr(
            "songhive.services.redis.get_sync_redis_client",
            MagicMock(side_effect=RuntimeError("redis down")),
        )
        assert scrobble_tasks._recently_submitted(config, "songhive:scrobble:np:u:t") is False
        # _mark_submitted swallows the error too.
        scrobble_tasks._mark_submitted(config, "songhive:scrobble:np:u:t", 60)
