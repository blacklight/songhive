"""Coverage: share page player + ref fallback for external-library tracks."""

import pytest

from songhive.models._enums import Visibility
from songhive.models.artist import Artist
from songhive.models.external_item import ExternalItem
from songhive.models.external_library import ExternalLibrary
from songhive.models.external_track import ExternalTrack
from songhive.models.library import Library
from songhive.models.track import Track


def _create_share_url(client, regular_user, auth_headers, item_type, item_id, **extra):
    body = {"item_type": item_type, "item_id": item_id}
    body.update(extra)
    response = client.post(
        "/api/v1/share-urls",
        json=body,
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 201
    return response.json()


async def _make_library(db_session, owner, name, provider_type, capabilities):
    library = Library(name=name, owner_id=str(owner.id))
    db_session.add(library)
    await db_session.flush()
    ext_lib = ExternalLibrary(
        name=name,
        provider_type=provider_type,
        library_id=str(library.id),
        created_by_id=str(owner.id),
        config={},
        capabilities=capabilities,
    )
    db_session.add(ext_lib)
    await db_session.flush()
    return ext_lib


@pytest.fixture
async def local_track(db_session, regular_user):
    """Private track backed by an active local-filesystem external library."""
    ext_lib = await _make_library(
        db_session, regular_user, "localfs", "local", {"read_bytes": True, "range_read": True}
    )
    artist = Artist(name="FS Artist")
    db_session.add(artist)
    await db_session.flush()
    track = Track(
        title="FS Track",
        artist_id=artist.id,
        audio_file_id=None,
        owner_id=str(regular_user.id),
        visibility=Visibility.PRIVATE.value,
        source="external",
    )
    db_session.add(track)
    await db_session.flush()
    db_session.add(
        ExternalTrack(
            track_id=str(track.id),
            external_library_id=str(ext_lib.id),
            provider_key="music/song.mp3",
            state="active",
        )
    )
    await db_session.flush()
    return track


@pytest.fixture(autouse=True)
def disable_rate_limit(client):
    client.app.state.config.auth.rate_limit_enabled = False


@pytest.mark.asyncio
async def test_streamable_ids_local(client, db_session, regular_user, auth_headers, local_track):
    from songhive.services import music
    from songhive.services.streaming import streamable_external_track_ids

    track = await music.get_track(db_session, str(local_track.id))
    ids = await streamable_external_track_ids(db_session, [track], None)
    assert str(track.id) in ids


def test_share_page_local_track_player(client, regular_user, auth_headers, local_track):
    data = _create_share_url(client, regular_user, auth_headers, "track", str(local_track.id))
    token = data["token"]
    response = client.get(f"/api/v1/share/{token}", follow_redirects=False)
    assert response.status_code == 200
    assert "<audio" in response.text


@pytest.mark.asyncio
async def test_streamable_ids_local_plus_entity(client, db_session, regular_user, auth_headers, local_track):
    """Track linked to both a local file-backed ref and an entity ref: local wins."""
    from songhive.services import music
    from songhive.services.streaming import streamable_external_track_ids

    tidal = await _make_library(db_session, regular_user, "tidal", "tidal", {"stream_url": True})
    db_session.add(
        ExternalItem(
            kind="track",
            track_id=str(local_track.id),
            external_library_id=str(tidal.id),
            provider_key="12345",
            state="active",
        )
    )
    await db_session.flush()

    track = await music.get_track(db_session, str(local_track.id))
    ids = await streamable_external_track_ids(db_session, [track], None)
    assert str(track.id) in ids


@pytest.mark.asyncio
async def test_streamable_ids_missing_local_falls_back_to_entity(
    client, db_session, regular_user, auth_headers, local_track
):
    """An inactive file-backed ref must not mask a servable entity ref."""
    from songhive.services import music
    from songhive.services.streaming import streamable_external_track_ids

    track0 = await music.get_track(db_session, str(local_track.id))
    track0.external_track.state = "missing"
    tidal = await _make_library(db_session, regular_user, "tidal", "tidal", {"stream_url": True})
    db_session.add(
        ExternalItem(
            kind="track",
            track_id=str(local_track.id),
            external_library_id=str(tidal.id),
            provider_key="12345",
            state="active",
        )
    )
    await db_session.flush()

    # Anonymous: tidal default stream_policy is "owner" → denied → not streamable.
    track = await music.get_track(db_session, str(local_track.id))
    ids = await streamable_external_track_ids(db_session, [track], None)
    assert str(track.id) not in ids

    # The owner is admitted by the policy → streamable via the entity ref.
    ids = await streamable_external_track_ids(db_session, [track], regular_user)
    assert str(track.id) in ids


@pytest.mark.asyncio
async def test_streamable_ids_disabled_local_falls_back(client, db_session, regular_user, auth_headers, local_track):
    """A disabled file-backed library must not mask a servable entity ref."""
    from songhive.services import music
    from songhive.services.streaming import streamable_external_track_ids

    track0 = await music.get_track(db_session, str(local_track.id))
    track0.external_track.external_library.enabled = False
    fake = await _make_library(db_session, regular_user, "fake", "fake", {"read_bytes": True})
    db_session.add(
        ExternalItem(
            kind="track",
            track_id=str(local_track.id),
            external_library_id=str(fake.id),
            provider_key="k",
            state="active",
        )
    )
    await db_session.flush()

    track = await music.get_track(db_session, str(local_track.id))
    ids = await streamable_external_track_ids(db_session, [track], None)
    assert str(track.id) in ids


@pytest.mark.asyncio
async def test_streamable_ids_local_wins_over_entity(client, db_session, regular_user, auth_headers, local_track):
    """Provider-ordering sanity: file-backed refs sort before entity refs."""
    from songhive.services.streaming import _external_refs_by_track

    tidal = await _make_library(db_session, regular_user, "tidal", "tidal", {"stream_url": True})
    db_session.add(
        ExternalItem(
            kind="track",
            track_id=str(local_track.id),
            external_library_id=str(tidal.id),
            provider_key="12345",
            state="active",
        )
    )
    await db_session.flush()

    refs = (await _external_refs_by_track(db_session, [str(local_track.id)]))[str(local_track.id)]
    assert isinstance(refs[0], ExternalTrack)
    assert isinstance(refs[1], ExternalItem)
