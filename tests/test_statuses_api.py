"""
Status API tests - ``POST /api/v1/statuses`` creates standalone statuses as
``create`` activities on the author's ``user`` entity, with Markdown/plain
rendering, mentions, language, and file/track attachments.
"""

import hashlib
import io
import secrets
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select

from songhive.models import Visibility
from songhive.models.activity import Activity, ActivityMention
from songhive.models.artist import Artist
from songhive.models.library import Library
from songhive.models.library_track import LibraryTrack
from songhive.models.notification import Notification
from songhive.models.share_grant import ShareGrant
from songhive.models.stored_file import StoredFile
from songhive.models.track import Track
from songhive.models.user import User
from songhive.services.metadata import AudioMetadata


async def _make_artist(session, name: str = "Test Artist") -> Artist:
    """Create and persist a test artist."""
    artist = Artist(name=name)
    session.add(artist)
    await session.flush()
    return artist


async def _make_track(
    session,
    owner: User | None,
    visibility: str = Visibility.PUBLIC.value,
    audio_file: StoredFile | None = None,
) -> Track:
    """Create and persist a test track."""
    artist = await _make_artist(session)
    track = Track(
        title="Test Track",
        artist_id=artist.id,
        owner_id=owner.id if owner is not None else None,
        visibility=visibility,
        audio_file_id=audio_file.id if audio_file is not None else None,
    )
    session.add(track)
    await session.flush()
    return track


async def _make_file(
    session,
    owner: User | None = None,
    visibility: str = Visibility.PRIVATE.value,
    seed: bytes | None = None,
) -> StoredFile:
    """Create and persist a test stored file."""
    if seed is None:
        seed = secrets.token_bytes(16)
    sha = hashlib.sha256(seed).hexdigest()
    stored_file = StoredFile(
        storage_path=f"files/{sha[:2]}/{sha[2:4]}/{sha}",
        storage_backend="local",
        content_type="image/png",
        size=len(seed),
        sha256=sha,
        owner_id=owner.id if owner is not None else None,
        visibility=visibility,
        original_filename="cover.png",
    )
    session.add(stored_file)
    await session.flush()
    return stored_file


def _post(client, user, auth_headers, **kwargs):
    return client.post("/api/v1/statuses/", json=kwargs, headers=auth_headers(user))


@pytest.mark.asyncio
async def test_create_status_markdown(client, db_session, regular_user, auth_headers):
    """A Markdown status renders safe HTML and persists the content type."""
    resp = _post(
        client,
        regular_user,
        auth_headers,
        status="hello **world** #music",
        language="en",
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body["entity_type"] == "user"
    assert body["entity_id"] == regular_user.id
    assert body["activity_type"] == "create"
    assert body["source_type"] == "local"
    assert body["content_type"] == "text/markdown"
    assert body["language"] == "en"
    assert "<strong>world</strong>" in body["content"]
    assert 'rel="tag">#music</a>' in body["content"]
    assert body["content_source"] == "hello **world** #music"

    obj = (await db_session.execute(select(Activity).where(Activity.id == body["id"]))).scalar_one().payload["object"]
    assert obj["type"] == "Note"
    assert obj["id"] == body["source_id"]
    assert obj["contentMap"] == {"en": body["content"]}
    assert any(tag.get("type") == "Hashtag" and tag.get("name") == "#music" for tag in obj.get("tag", []))


@pytest.mark.asyncio
async def test_create_status_plain_escapes_html(client, db_session, regular_user, auth_headers):
    """A plain-text status escapes markup and keeps the source verbatim."""
    resp = _post(
        client,
        regular_user,
        auth_headers,
        status="<script>alert(1)</script>",
        content_type="text/plain",
    )

    assert resp.status_code == 201
    body = resp.json()
    assert "<script>" not in body["content"]
    assert "&lt;script&gt;" in body["content"]
    assert body["content_type"] == "text/plain"


@pytest.mark.asyncio
async def test_create_status_requires_content_or_attachment(client, regular_user, auth_headers):
    """An empty status without attachments is rejected."""
    resp = _post(client, regular_user, auth_headers, status="   ")
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_status_invalid_content_type(client, regular_user, auth_headers):
    """Unsupported content types are rejected."""
    resp = _post(client, regular_user, auth_headers, status="hi", content_type="text/html")
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_status_invalid_language(client, regular_user, auth_headers):
    """Malformed language tags are rejected."""
    resp = _post(client, regular_user, auth_headers, status="hi", language="not a tag!!")
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_status_mention(client, db_session, regular_user, other_user, auth_headers):
    """A ``@user`` mention resolves to a local user, tags, and notifies."""
    resp = _post(client, regular_user, auth_headers, status="hey @other check this")

    assert resp.status_code == 201
    activity_id = resp.json()["id"]

    mention = (
        await db_session.execute(select(ActivityMention).where(ActivityMention.activity_id == activity_id))
    ).scalar_one()
    assert mention.handle == "@other"
    assert mention.user_id == other_user.id

    notification = (
        await db_session.execute(
            select(Notification).where(
                Notification.user_id == other_user.id,
                Notification.type == "mention",
            )
        )
    ).scalar_one_or_none()
    assert notification is not None


@pytest.mark.asyncio
async def test_create_status_media_attachment(client, db_session, regular_user, auth_headers):
    """An owned file becomes a Document attachment and is made public."""
    stored = await _make_file(db_session, owner=regular_user)

    resp = _post(client, regular_user, auth_headers, status="with pic", media_ids=[stored.id])

    assert resp.status_code == 201
    attachments = resp.json()["attachments"]
    assert len(attachments) == 1
    assert attachments[0]["type"] == "Document"
    assert attachments[0]["mediaType"] == "image/png"
    assert attachments[0]["name"] == "cover.png"

    await db_session.refresh(stored)
    assert stored.visibility == Visibility.PUBLIC.value


@pytest.mark.asyncio
async def test_create_status_media_requires_ownership(client, db_session, regular_user, other_user, auth_headers):
    """Files owned by another user cannot be attached."""
    stored = await _make_file(db_session, owner=other_user)

    resp = _post(client, regular_user, auth_headers, status="with pic", media_ids=[stored.id])
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_create_status_track_attachment(client, db_session, regular_user, auth_headers):
    """A hosted track becomes an Audio attachment on the status object."""
    audio = await _make_file(db_session, owner=regular_user)
    track = await _make_track(db_session, regular_user, audio_file=audio)

    resp = _post(client, regular_user, auth_headers, status="listen", track_ids=[track.id])

    assert resp.status_code == 201
    attachments = resp.json()["attachments"]
    assert len(attachments) == 1
    assert attachments[0]["type"] == "Audio"
    assert attachments[0]["name"] == "Test Artist - Test Track"
    assert f"/api/v1/files/{audio.id}/download" in attachments[0]["url"]


@pytest.mark.asyncio
async def test_create_status_track_access_required(client, db_session, regular_user, other_user, auth_headers):
    """Tracks the author cannot access cannot be attached."""
    track = await _make_track(db_session, other_user, visibility=Visibility.PRIVATE.value)

    resp = _post(client, regular_user, auth_headers, status="listen", track_ids=[track.id])
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_create_status_attachment_only(client, db_session, regular_user, auth_headers):
    """A status with only attachments and no text is accepted."""
    track = await _make_track(db_session, regular_user)

    resp = _post(client, regular_user, auth_headers, track_ids=[track.id])
    assert resp.status_code == 201
    assert resp.json()["attachments"]


@pytest.mark.asyncio
async def test_create_status_mentioned_grants_file_access(client, db_session, regular_user, other_user, auth_headers):
    """Mentioned-visibility statuses grant mentioned users access to files."""
    stored = await _make_file(db_session, owner=regular_user)

    resp = _post(
        client,
        regular_user,
        auth_headers,
        status="for you @other",
        visibility="mentioned",
        media_ids=[stored.id],
    )

    assert resp.status_code == 201
    await db_session.refresh(stored)
    assert stored.visibility == Visibility.PRIVATE.value

    grant = (
        await db_session.execute(
            select(ShareGrant).where(
                ShareGrant.item_type == "file",
                ShareGrant.item_id == stored.id,
                ShareGrant.user_id == other_user.id,
            )
        )
    ).scalar_one_or_none()
    assert grant is not None


@pytest.mark.asyncio
async def test_create_status_unauthenticated(client):
    """Posting a status requires authentication."""
    resp = client.post("/api/v1/statuses/", json={"status": "hi"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_create_status_visible_on_profile_feed(client, db_session, regular_user, auth_headers):
    """Statuses appear in the author's profile posts feed."""
    resp = _post(client, regular_user, auth_headers, status="on my profile")
    assert resp.status_code == 201

    feed = client.get(f"/api/v1/users/{regular_user.username}/activities")
    assert feed.status_code == 200
    ids = [a["id"] for a in feed.json()["activities"]]
    assert resp.json()["id"] in ids


@pytest.mark.asyncio
async def test_create_status_audit(client, db_session, regular_user, auth_headers):
    """Posting a status records an audit log entry."""
    from songhive.models.audit_log import AuditLog

    resp = _post(client, regular_user, auth_headers, status="audited")
    assert resp.status_code == 201

    entry = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.action == "status.create",
                AuditLog.target_id == resp.json()["id"],
            )
        )
    ).scalar_one_or_none()
    assert entry is not None
    assert entry.actor_id == regular_user.id


@pytest.mark.asyncio
async def test_update_profile_status_content_type(client, db_session, regular_user, auth_headers):
    """The default status content type is a patchable profile field."""
    resp = client.patch(
        "/api/v1/users/me",
        json={"status_content_type": "text/plain"},
        headers=auth_headers(regular_user),
    )
    assert resp.status_code == 200
    assert resp.json()["status_content_type"] == "text/plain"

    resp = client.patch(
        "/api/v1/users/me",
        json={"status_content_type": "text/html"},
        headers=auth_headers(regular_user),
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Status edits (PATCH /api/v1/activities/{id})
# ---------------------------------------------------------------------------


def _patch(client, user, auth_headers, activity_id, **kwargs):
    return client.patch(
        f"/api/v1/activities/{activity_id}",
        json=kwargs,
        headers=auth_headers(user),
    )


async def _object(db_session, activity_id: str) -> dict:
    activity = (await db_session.execute(select(Activity).where(Activity.id == activity_id))).scalar_one()
    return activity.payload["object"]


@pytest.mark.asyncio
async def test_update_status_attachments(client, db_session, regular_user, auth_headers):
    """Attachment edits rebuild the object's attachment list with markers."""
    stored = await _make_file(db_session, owner=regular_user)
    track = await _make_track(db_session, regular_user)

    created = _post(client, regular_user, auth_headers, status="with stuff", media_ids=[stored.id])
    assert created.status_code == 201
    activity_id = created.json()["id"]

    obj = await _object(db_session, activity_id)
    assert obj["attachment"][0]["songhive:fileId"] == stored.id

    resp = _patch(
        client,
        regular_user,
        auth_headers,
        activity_id,
        media_ids=[],
        track_ids=[track.id],
    )

    assert resp.status_code == 200
    attachments = resp.json()["attachments"]
    assert len(attachments) == 1
    assert attachments[0]["songhive:trackId"] == track.id
    assert attachments[0]["type"] == "Document"  # no audio file → page link

    obj = await _object(db_session, activity_id)
    assert "updated" in obj


@pytest.mark.asyncio
async def test_update_status_media_requires_ownership(client, db_session, regular_user, other_user, auth_headers):
    """Attachment edits re-check file ownership."""
    other_file = await _make_file(db_session, owner=other_user)
    created = _post(client, regular_user, auth_headers, status="mine")
    activity_id = created.json()["id"]

    resp = _patch(client, regular_user, auth_headers, activity_id, media_ids=[other_file.id])
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_status_clear_attachments(client, db_session, regular_user, auth_headers):
    """An empty id list removes user-managed attachments but keeps the text."""
    stored = await _make_file(db_session, owner=regular_user)
    created = _post(client, regular_user, auth_headers, status="with pic", media_ids=[stored.id])
    activity_id = created.json()["id"]

    resp = _patch(client, regular_user, auth_headers, activity_id, media_ids=[], track_ids=[])

    assert resp.status_code == 200
    assert resp.json()["attachments"] == []
    assert resp.json()["content"]


@pytest.mark.asyncio
async def test_update_status_empty_rejected(client, db_session, regular_user, auth_headers):
    """A status cannot be edited down to no text and no attachments."""
    stored = await _make_file(db_session, owner=regular_user)
    created = _post(client, regular_user, auth_headers, status="", media_ids=[stored.id])
    assert created.status_code == 201
    activity_id = created.json()["id"]

    resp = _patch(client, regular_user, auth_headers, activity_id, content="", media_ids=[], track_ids=[])
    assert resp.status_code == 422

    # Removing the only attachment while text stays empty is also rejected.
    resp = _patch(client, regular_user, auth_headers, activity_id, media_ids=[], track_ids=[])
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_update_status_language(client, db_session, regular_user, auth_headers):
    """Language edits update the object's contentMap; null clears it."""
    created = _post(client, regular_user, auth_headers, status="hello", language="en")
    activity_id = created.json()["id"]

    resp = _patch(client, regular_user, auth_headers, activity_id, language="fr")
    assert resp.status_code == 200
    assert resp.json()["language"] == "fr"
    obj = await _object(db_session, activity_id)
    assert obj["contentMap"] == {"fr": obj["content"]}

    resp = _patch(client, regular_user, auth_headers, activity_id, language=None)
    assert resp.status_code == 200
    assert resp.json()["language"] is None
    obj = await _object(db_session, activity_id)
    assert "contentMap" not in obj


@pytest.mark.asyncio
async def test_update_status_content_type_switch(client, db_session, regular_user, auth_headers):
    """Switching format re-renders the existing source with the new type."""
    created = _post(client, regular_user, auth_headers, status="**bold**", content_type="text/markdown")
    activity_id = created.json()["id"]
    assert "<strong>" in created.json()["content"]

    resp = _patch(client, regular_user, auth_headers, activity_id, content_type="text/plain")
    assert resp.status_code == 200
    body = resp.json()
    assert body["content_type"] == "text/plain"
    assert "<strong>" not in body["content"]
    assert "**bold**" in body["content"]


@pytest.mark.asyncio
async def test_update_status_invalid_content_type(client, regular_user, auth_headers):
    """Unsupported content types are rejected on edit too."""
    created = _post(client, regular_user, auth_headers, status="hi")
    resp = _patch(client, regular_user, auth_headers, created.json()["id"], content_type="text/html")
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_update_status_unmentioned_fields_untouched(client, db_session, regular_user, auth_headers):
    """Omitted fields keep their values — attachments survive a text edit."""
    stored = await _make_file(db_session, owner=regular_user)
    track = await _make_track(db_session, regular_user)
    created = _post(
        client,
        regular_user,
        auth_headers,
        status="first",
        language="en",
        media_ids=[stored.id],
        track_ids=[track.id],
    )
    activity_id = created.json()["id"]

    resp = _patch(client, regular_user, auth_headers, activity_id, content="second")

    assert resp.status_code == 200
    body = resp.json()
    assert body["content_source"] == "second"
    assert body["language"] == "en"
    assert len(body["attachments"]) == 2


@pytest.mark.asyncio
async def test_update_status_preserves_entity_attachment(client, db_session, regular_user, auth_headers):
    """Track-publication attachments keep the entity-owned track doc."""
    created = _post(client, regular_user, auth_headers, status="a status")
    activity_id = created.json()["id"]

    # Simulate a track-publication Note: entity-owned attachment without a
    # ``songhive:`` marker (the shared track itself) alongside none from the
    # user. Editing attachments must keep the unmarked doc.
    activity = (await db_session.execute(select(Activity).where(Activity.id == activity_id))).scalar_one()
    activity.payload["object"]["attachment"] = [
        {"type": "Audio", "mediaType": "audio/mpeg", "url": "https://x/stream", "name": "Track"}
    ]
    from sqlalchemy.orm.attributes import flag_modified

    flag_modified(activity, "payload")
    await db_session.flush()

    new_file = await _make_file(db_session, owner=regular_user)
    resp = _patch(client, regular_user, auth_headers, activity_id, media_ids=[new_file.id])

    assert resp.status_code == 200
    attachments = resp.json()["attachments"]
    assert len(attachments) == 2
    assert attachments[0]["name"] == "Track"
    assert attachments[0].get("songhive:fileId") is None
    assert attachments[1]["songhive:fileId"] == new_file.id


# ---------------------------------------------------------------------------
# Audio attachment library import (``audio_import`` options)
# ---------------------------------------------------------------------------


def _upload_audio(client, user, auth_headers, tmp_path, content=b"fake audio"):
    """Upload a fake audio file as a plain stored file and return its id."""
    client.app.state.config.storage.local_path = tmp_path / "media"
    resp = client.post(
        "/api/v1/files/upload?import_audio=false",
        files={"file": ("song.mp3", io.BytesIO(content), "audio/mpeg")},
        headers=auth_headers(user),
    )
    assert resp.status_code == 200
    assert "X-Track-Id" not in resp.headers
    return resp.json()["id"]


def _mock_metadata(monkeypatch, title="Posted Song"):
    monkeypatch.setattr(
        "songhive.services.import_.extract_metadata",
        lambda _: AudioMetadata(title=title, artist="Poster", mimetype="audio/mpeg"),
    )


async def _track_for_file(db_session, file_id: str) -> Track | None:
    return (await db_session.execute(select(Track).where(Track.audio_file_id == file_id))).scalar_one_or_none()


async def _library_track(db_session, library: Library, track: Track) -> LibraryTrack | None:
    return (
        await db_session.execute(
            select(LibraryTrack).where(
                LibraryTrack.library_id == library.id,
                LibraryTrack.track_id == str(track.id),
            )
        )
    ).scalar_one_or_none()


@pytest.mark.asyncio
async def test_create_status_audio_attachment_imports_to_uploads(
    client, db_session, regular_user, auth_headers, monkeypatch, tmp_path
):
    """An attached audio file is imported into the Uploads library by default."""
    _mock_metadata(monkeypatch)
    file_id = _upload_audio(client, regular_user, auth_headers, tmp_path)

    resp = _post(client, regular_user, auth_headers, status="new track", media_ids=[file_id])
    assert resp.status_code == 201

    track = await _track_for_file(db_session, file_id)
    assert track is not None
    assert track.title == "Posted Song"
    assert track.owner_id == regular_user.id

    library = (
        await db_session.execute(
            select(Library).where(
                Library.owner_id == regular_user.id,
                Library.name == "Uploads",
            )
        )
    ).scalar_one()
    assert await _library_track(db_session, library, track) is not None


@pytest.mark.asyncio
async def test_create_status_audio_import_disabled(
    client, db_session, regular_user, auth_headers, monkeypatch, tmp_path
):
    """``upload_to_library: false`` keeps the file a plain attachment."""
    _mock_metadata(monkeypatch)
    file_id = _upload_audio(client, regular_user, auth_headers, tmp_path)

    resp = _post(
        client,
        regular_user,
        auth_headers,
        status="attach only",
        media_ids=[file_id],
        audio_import={"upload_to_library": False},
    )
    assert resp.status_code == 201
    assert resp.json()["attachments"]

    assert await _track_for_file(db_session, file_id) is None
    libraries = (await db_session.execute(select(Library).where(Library.owner_id == regular_user.id))).scalars().all()
    assert list(libraries) == []


@pytest.mark.asyncio
async def test_create_status_audio_import_custom_library(
    client, db_session, regular_user, auth_headers, monkeypatch, tmp_path
):
    """``library_id`` imports into the named library instead of Uploads."""
    _mock_metadata(monkeypatch)
    library = Library(name="Collection", owner_id=regular_user.id)
    db_session.add(library)
    await db_session.flush()
    file_id = _upload_audio(client, regular_user, auth_headers, tmp_path)

    resp = _post(
        client,
        regular_user,
        auth_headers,
        status="to collection",
        media_ids=[file_id],
        audio_import={"library_id": library.id},
    )
    assert resp.status_code == 201

    track = await _track_for_file(db_session, file_id)
    assert track is not None
    assert await _library_track(db_session, library, track) is not None


@pytest.mark.asyncio
async def test_create_status_audio_import_unknown_library_rejected(
    client, db_session, regular_user, auth_headers, monkeypatch, tmp_path
):
    """An unknown ``library_id`` fails the request before the status is created."""
    _mock_metadata(monkeypatch)
    file_id = _upload_audio(client, regular_user, auth_headers, tmp_path)

    resp = _post(
        client,
        regular_user,
        auth_headers,
        status="bad library",
        media_ids=[file_id],
        audio_import={"library_id": "00000000-0000-0000-0000-000000000000"},
    )
    assert resp.status_code == 404
    assert await _track_for_file(db_session, file_id) is None


@pytest.mark.asyncio
async def test_create_status_audio_import_fetch_metadata_enqueues_enrichment(
    client, db_session, regular_user, auth_headers, monkeypatch, tmp_path
):
    """``fetch_metadata: true`` enqueues MusicBrainz enrichment for the track."""
    _mock_metadata(monkeypatch)
    enrich_mock = MagicMock()
    monkeypatch.setattr("songhive.tasks.musicbrainz.enrich_track", enrich_mock)
    file_id = _upload_audio(client, regular_user, auth_headers, tmp_path)

    resp = _post(
        client,
        regular_user,
        auth_headers,
        status="enrich me",
        media_ids=[file_id],
        audio_import={"fetch_metadata": True},
    )
    assert resp.status_code == 201

    track = await _track_for_file(db_session, file_id)
    assert track is not None
    enrich_mock.delay.assert_called_once_with(str(track.id))


@pytest.mark.asyncio
async def test_create_status_non_audio_attachment_not_imported(client, db_session, regular_user, auth_headers):
    """Non-audio attachments never trigger a library import."""
    stored = await _make_file(db_session, owner=regular_user)

    resp = _post(client, regular_user, auth_headers, status="pic", media_ids=[stored.id])
    assert resp.status_code == 201

    assert await _track_for_file(db_session, stored.id) is None
    libraries = (await db_session.execute(select(Library).where(Library.owner_id == regular_user.id))).scalars().all()
    assert list(libraries) == []


@pytest.mark.asyncio
async def test_reply_audio_attachment_imports(client, db_session, regular_user, auth_headers, monkeypatch, tmp_path):
    """Replies honour ``audio_import`` for their own audio attachments."""
    _mock_metadata(monkeypatch)
    file_id = _upload_audio(client, regular_user, auth_headers, tmp_path)
    parent = _post(client, regular_user, auth_headers, status="parent")

    resp = client.post(
        f"/api/v1/activities/{parent.json()['id']}/reply",
        json={"status": "with audio", "media_ids": [file_id]},
        headers=auth_headers(regular_user),
    )
    assert resp.status_code == 201

    track = await _track_for_file(db_session, file_id)
    assert track is not None


@pytest.mark.asyncio
async def test_update_status_audio_import_uses_current_attachments(
    client, db_session, regular_user, auth_headers, monkeypatch, tmp_path
):
    """PATCH ``audio_import`` applies to the activity's current file attachments."""
    _mock_metadata(monkeypatch)
    file_id = _upload_audio(client, regular_user, auth_headers, tmp_path)

    created = _post(
        client,
        regular_user,
        auth_headers,
        status="with audio",
        media_ids=[file_id],
        audio_import={"upload_to_library": False},
    )
    assert created.status_code == 201
    assert await _track_for_file(db_session, file_id) is None

    resp = _patch(
        client,
        regular_user,
        auth_headers,
        created.json()["id"],
        content="edited",
        audio_import={"upload_to_library": True},
    )
    assert resp.status_code == 200

    track = await _track_for_file(db_session, file_id)
    assert track is not None
