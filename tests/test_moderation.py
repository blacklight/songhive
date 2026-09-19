"""
Moderation tests — ``songhive.services.moderation`` plus the
``/api/v1/users/me/mutes|blocks`` and ``/api/v1/admin/moderation/*``
endpoints.

Mute is one-way (hidden from the muter only); block is reciprocal and
severs the follow relationship; admin ``limit`` degrades follows to
manual approval while ``suspend`` severs every local relationship and
hides the actor; instance policies layer database defederation and
followers-only gating over the configured block lists.
"""

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from songhive.models._enums import Visibility
from songhive.models.activity import Activity
from songhive.models.audit_log import AuditLog
from songhive.models.follow import FOLLOW_STATE_ACCEPTED, Follow
from songhive.models.moderation import (
    ADMIN_ACTION_LIMIT,
    ADMIN_ACTION_SUSPEND,
    INSTANCE_ACTION_DEFEDERATE,
    INSTANCE_ACTION_FOLLOWERS_ONLY,
    USER_MODERATION_BLOCK,
    USER_MODERATION_MUTE,
)
from songhive.models.notification import NotificationType
from songhive.services import federation as federation_service
from songhive.services import moderation as moderation_service


def _make_activity(owner, source_actor: str, **overrides) -> Activity:
    """Build a minimally valid activity row for visibility checks."""
    params = {
        "entity_type": "track",
        "entity_id": "t1",
        "activity_type": "create",
        "source_type": "local",
        "source_actor": source_actor,
        "source_id": f"{source_actor}/objects/1",
        "visibility": Visibility.PUBLIC.value,
        "owner_user_id": owner.id,
    }
    params.update(overrides)
    return Activity(**params)


def _follow_row(user, target_actor_url: str, target_user_id=None, state=FOLLOW_STATE_ACCEPTED) -> Follow:
    return Follow(
        user_id=user.id,
        actor_url=user.actor_url or f"urn:songhive:user:{user.username}",
        target_actor_url=target_actor_url,
        target_user_id=target_user_id,
        state=state,
    )


# ---------------------------------------------------------------------------
# User-level mute/block — service behavior
# ---------------------------------------------------------------------------


async def test_mute_hides_activity_for_muter_only(db_session, regular_user, other_user, admin_user):
    """Muted actors vanish from the muter's view but nobody else's."""
    target_url = "urn:songhive:user:other"
    await moderation_service.set_user_moderation(
        db_session,
        regular_user,
        target_actor_url=target_url,
        target_user_id=other_user.id,
        kind=USER_MODERATION_MUTE,
    )

    activity = _make_activity(other_user, target_url)
    muter_ctx = await moderation_service.moderation_context(db_session, regular_user)
    assert moderation_service.activity_hidden(muter_ctx, activity)

    bystander_ctx = await moderation_service.moderation_context(db_session, admin_user)
    assert not moderation_service.activity_hidden(bystander_ctx, activity)


async def test_mute_is_one_way(db_session, regular_user, other_user):
    """The muted user still sees the muter's activities."""
    target_url = "urn:songhive:user:other"
    muter_url = "urn:songhive:user:regular"
    await moderation_service.set_user_moderation(
        db_session,
        regular_user,
        target_actor_url=target_url,
        target_user_id=other_user.id,
        kind=USER_MODERATION_MUTE,
    )
    activity = _make_activity(regular_user, muter_url)
    muted_ctx = await moderation_service.moderation_context(db_session, other_user)
    assert not moderation_service.activity_hidden(muted_ctx, activity)


async def test_block_is_reciprocal_and_severs_follows(db_session, config, regular_user, other_user):
    """A block hides both directions and deletes the follow rows between them."""
    regular_url = "urn:songhive:user:regular"
    other_url = "urn:songhive:user:other"
    db_session.add(_follow_row(regular_user, other_url, other_user.id))
    db_session.add(_follow_row(other_user, regular_url, regular_user.id))
    await db_session.flush()

    await moderation_service.set_user_moderation(
        db_session,
        regular_user,
        target_actor_url=other_url,
        target_user_id=other_user.id,
        kind=USER_MODERATION_BLOCK,
    )
    await moderation_service.sever_block_relationship(
        db_session,
        config,
        regular_user,
        target_actor_url=other_url,
        target_user_id=other_user.id,
    )

    assert await moderation_service.block_exists_between(
        db_session, regular_user, target_actor_url=other_url, target_user_id=other_user.id
    )
    assert await moderation_service.block_exists_between(
        db_session, other_user, target_actor_url=regular_url, target_user_id=regular_user.id
    )

    remaining = (await db_session.execute(select(Follow))).scalars().all()
    assert remaining == []

    # Both directions of visibility are cut.
    activity = _make_activity(other_user, other_url)
    blocker_ctx = await moderation_service.moderation_context(db_session, regular_user)
    assert moderation_service.activity_hidden(blocker_ctx, activity)
    blocked_ctx = await moderation_service.moderation_context(db_session, other_user)
    activity2 = _make_activity(regular_user, regular_url)
    assert moderation_service.activity_hidden(blocked_ctx, activity2)


async def test_block_prevents_interaction(db_session, regular_user, other_user):
    """``assert_interaction_allowed`` rejects interactions across a block."""
    other_url = "urn:songhive:user:other"
    await moderation_service.set_user_moderation(
        db_session,
        regular_user,
        target_actor_url=other_url,
        target_user_id=other_user.id,
        kind=USER_MODERATION_BLOCK,
    )
    activity = _make_activity(regular_user, "urn:songhive:user:regular")
    with pytest.raises(HTTPException) as exc:
        await moderation_service.assert_interaction_allowed(db_session, other_user, activity)
    assert exc.value.status_code == 403


# ---------------------------------------------------------------------------
# Admin-level limit/suspend — service behavior
# ---------------------------------------------------------------------------


async def test_suspend_hides_everything_and_severs_follows(db_session, config, regular_user, other_user, admin_user):
    """Suspension hides the actor's content and removes their follow rows."""
    other_url = "urn:songhive:user:other"
    db_session.add(_follow_row(regular_user, other_url, other_user.id))
    db_session.add(_follow_row(other_user, "urn:songhive:user:regular", regular_user.id))
    await db_session.flush()

    await moderation_service.set_admin_user_moderation(
        db_session,
        target_actor_url=other_url,
        target_user_id=other_user.id,
        action=ADMIN_ACTION_SUSPEND,
        reason="spam",
        actor_data=None,
        admin=admin_user,
    )
    removed = await moderation_service.sever_relationships(
        db_session, config, actor_url=other_url, user_id=other_user.id
    )
    assert removed == 2
    assert (await db_session.execute(select(Follow))).scalars().all() == []

    assert await moderation_service.user_is_suspended(db_session, other_user.id)
    assert await moderation_service.actor_is_suspended(db_session, other_url)

    activity = _make_activity(other_user, other_url)
    for viewer in (regular_user, admin_user, None):
        ctx = await moderation_service.moderation_context(db_session, viewer)
        assert moderation_service.activity_hidden(ctx, activity)


async def test_suspended_author_cannot_interact(db_session, regular_user, other_user, admin_user):
    """A suspended local user is blocked from interacting with anything."""
    await moderation_service.set_admin_user_moderation(
        db_session,
        target_actor_url="urn:songhive:user:other",
        target_user_id=other_user.id,
        action=ADMIN_ACTION_SUSPEND,
        reason=None,
        actor_data=None,
        admin=admin_user,
    )
    activity = _make_activity(regular_user, "urn:songhive:user:regular")
    with pytest.raises(HTTPException) as exc:
        await moderation_service.assert_interaction_allowed(db_session, other_user, activity)
    assert exc.value.status_code == 403


async def test_admin_action_updates_idempotently(db_session, other_user, admin_user):
    """Applying a second action to the same actor updates the single row."""
    other_url = "urn:songhive:user:other"
    await moderation_service.set_admin_user_moderation(
        db_session,
        target_actor_url=other_url,
        target_user_id=other_user.id,
        action=ADMIN_ACTION_LIMIT,
        reason="first",
        actor_data=None,
        admin=admin_user,
    )
    row = await moderation_service.set_admin_user_moderation(
        db_session,
        target_actor_url=other_url,
        target_user_id=other_user.id,
        action=ADMIN_ACTION_SUSPEND,
        reason="escalated",
        actor_data=None,
        admin=admin_user,
    )
    assert row.action == ADMIN_ACTION_SUSPEND
    assert row.reason == "escalated"
    assert len(await moderation_service.list_admin_user_moderations(db_session)) == 1
    assert await moderation_service.user_is_suspended(db_session, other_user.id)
    assert not await moderation_service.user_is_limited(db_session, other_user.id)


async def test_limited_actor_gated_to_followers_and_reveal(db_session, regular_user, other_user, admin_user):
    """A limited actor's content only reaches followers — or explicit reveals."""
    other_url = "urn:songhive:user:other"
    await moderation_service.set_admin_user_moderation(
        db_session,
        target_actor_url=other_url,
        target_user_id=other_user.id,
        action=ADMIN_ACTION_LIMIT,
        reason=None,
        actor_data=None,
        admin=admin_user,
    )
    activity = _make_activity(other_user, other_url)

    # Non-followers and anonymous viewers don't see it.
    stranger_ctx = await moderation_service.moderation_context(db_session, admin_user)
    assert moderation_service.activity_hidden(stranger_ctx, activity)
    anon_ctx = await moderation_service.moderation_context(db_session, None)
    assert moderation_service.activity_hidden(anon_ctx, activity)

    # Accepted followers do.
    db_session.add(_follow_row(regular_user, other_url, other_user.id))
    await db_session.flush()
    follower_ctx = await moderation_service.moderation_context(db_session, regular_user)
    assert not moderation_service.activity_hidden(follower_ctx, activity)

    # So does the limited user themselves.
    own_ctx = await moderation_service.moderation_context(db_session, other_user)
    assert not moderation_service.activity_hidden(own_ctx, activity)

    # An explicit reveal lifts only the gate for that actor.
    assert not moderation_service.activity_hidden(stranger_ctx, activity, other_url)


async def test_limited_profile_activities_reveal_param(
    client, db_session, regular_user, other_user, admin_user, auth_headers
):
    """``?reveal=1`` exposes a limited local user's timeline to non-followers."""
    other_url = moderation_service.local_actor_url(other_user)
    await moderation_service.set_admin_user_moderation(
        db_session,
        target_actor_url=other_url,
        target_user_id=other_user.id,
        action=ADMIN_ACTION_LIMIT,
        reason=None,
        actor_data=None,
        admin=admin_user,
    )
    db_session.add(_make_activity(other_user, other_url))
    await db_session.commit()

    headers = auth_headers(regular_user)
    hidden = client.get("/api/v1/users/other/activities", headers=headers).json()
    assert hidden["activities"] == []

    revealed = client.get("/api/v1/users/other/activities?reveal=1", headers=headers).json()
    assert len(revealed["activities"]) == 1


# ---------------------------------------------------------------------------
# Instance-level moderation — service behavior
# ---------------------------------------------------------------------------


async def test_defederation_blocks_domain_and_hides_actors(db_session, config, regular_user, admin_user):
    """A defederate policy makes the domain blocked and its activities hidden."""
    await moderation_service.set_instance_moderation(
        db_session,
        domain="bad.example",
        action=INSTANCE_ACTION_DEFEDERATE,
        reason="abuse",
        admin=admin_user,
    )
    policies = await moderation_service.load_instance_policies(db_session)
    assert policies == {"bad.example": INSTANCE_ACTION_DEFEDERATE}
    assert federation_service.is_domain_blocked("bad.example", config)

    activity = _make_activity(regular_user, "https://bad.example/users/spam")
    ctx = await moderation_service.moderation_context(db_session, regular_user)
    assert moderation_service.activity_hidden(ctx, activity)

    anon_ctx = await moderation_service.moderation_context(db_session, None)
    assert moderation_service.activity_hidden(anon_ctx, activity)


async def test_followers_only_gates_to_followers(db_session, regular_user, other_user, admin_user):
    """Followers-only domains are visible only to local users who follow them."""
    await moderation_service.set_instance_moderation(
        db_session,
        domain="quiet.example",
        action=INSTANCE_ACTION_FOLLOWERS_ONLY,
        reason=None,
        admin=admin_user,
    )
    source = "https://quiet.example/users/carol"
    activity = _make_activity(other_user, source)

    # Anonymous and non-following viewers cannot see it.
    assert moderation_service.activity_hidden(await moderation_service.moderation_context(db_session, None), activity)
    ctx = await moderation_service.moderation_context(db_session, regular_user)
    assert moderation_service.activity_hidden(ctx, activity)

    # A local user who follows the actor still sees it.
    db_session.add(_follow_row(regular_user, source))
    await db_session.flush()
    ctx = await moderation_service.moderation_context(db_session, regular_user)
    assert not moderation_service.activity_hidden(ctx, activity)


async def test_followers_only_does_not_block_domain(db_session, admin_user):
    """Followers-only is a visibility gate, not a federation block."""
    await moderation_service.set_instance_moderation(
        db_session,
        domain="quiet.example",
        action=INSTANCE_ACTION_FOLLOWERS_ONLY,
        reason=None,
        admin=admin_user,
    )
    await moderation_service.load_instance_policies(db_session)
    assert federation_service.db_domain_policy("quiet.example") == INSTANCE_ACTION_FOLLOWERS_ONLY


# ---------------------------------------------------------------------------
# Notifications + target resolution
# ---------------------------------------------------------------------------


async def test_notification_suppressed(db_session, regular_user, other_user, admin_user):
    """Muted/blocked/suspended sources never reach the recipient."""
    other_url = "urn:songhive:user:other"
    assert not await moderation_service.notification_suppressed(db_session, regular_user, other_url)

    await moderation_service.set_user_moderation(
        db_session,
        regular_user,
        target_actor_url=other_url,
        target_user_id=other_user.id,
        kind=USER_MODERATION_MUTE,
    )
    assert await moderation_service.notification_suppressed(db_session, regular_user, other_url)

    await moderation_service.set_admin_user_moderation(
        db_session,
        target_actor_url="urn:songhive:user:regular",
        target_user_id=regular_user.id,
        action=ADMIN_ACTION_SUSPEND,
        reason=None,
        actor_data=None,
        admin=admin_user,
    )
    # A suspended recipient receives nothing at all.
    assert await moderation_service.notification_suppressed(db_session, regular_user, None)


async def test_notification_suppressed_limited_actor(db_session, regular_user, other_user, admin_user):
    """Limited actors only notify followers; reports bypass the gate."""
    other_url = "urn:songhive:user:other"
    await moderation_service.set_admin_user_moderation(
        db_session,
        target_actor_url=other_url,
        target_user_id=other_user.id,
        action=ADMIN_ACTION_LIMIT,
        reason=None,
        actor_data=None,
        admin=admin_user,
    )

    assert await moderation_service.notification_suppressed(
        db_session, regular_user, other_url, type=NotificationType.MENTION
    )
    # Reports are not filterable — a limited user's report still notifies.
    assert not await moderation_service.notification_suppressed(
        db_session, regular_user, other_url, type=NotificationType.REPORT
    )

    # Followers keep receiving the limited actor's notifications.
    db_session.add(_follow_row(regular_user, other_url, target_user_id=other_user.id))
    await db_session.flush()
    assert not await moderation_service.notification_suppressed(
        db_session, regular_user, other_url, type=NotificationType.MENTION
    )


async def test_notification_suppressed_followers_only_domain(db_session, regular_user, admin_user):
    """Actors on followers-only domains only notify their followers."""
    remote_url = "https://quiet.example/users/bob"
    await moderation_service.set_instance_moderation(
        db_session,
        domain="quiet.example",
        action=INSTANCE_ACTION_FOLLOWERS_ONLY,
        reason=None,
        admin=admin_user,
    )

    assert await moderation_service.notification_suppressed(
        db_session, regular_user, remote_url, type=NotificationType.MENTION
    )

    db_session.add(_follow_row(regular_user, remote_url))
    await db_session.flush()
    assert not await moderation_service.notification_suppressed(
        db_session, regular_user, remote_url, type=NotificationType.MENTION
    )


async def test_resolve_moderation_target_local_username(db_session, config, other_user):
    """Federation-disabled local users resolve to their URN actor id."""
    actor_url, local, actor_data = await moderation_service.resolve_moderation_target(db_session, config, "other")
    assert actor_url == "urn:songhive:user:other"
    assert local.id == other_user.id
    assert actor_data["preferredUsername"] == "other"


# ---------------------------------------------------------------------------
# API: /users/me/mutes + /users/me/blocks
# ---------------------------------------------------------------------------


def test_mute_api_roundtrip(client, regular_user, other_user, auth_headers):
    """Mute a local user via API, see them listed and flagged, then undo."""
    headers = auth_headers(regular_user)
    resp = client.post("/api/v1/users/me/mutes", json={"actor_url": "other"}, headers=headers)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["actor_url"] == "urn:songhive:user:other"
    assert body["local_username"] == "other"

    listed = client.get("/api/v1/users/me/mutes", headers=headers)
    assert listed.status_code == 200
    assert [row["actor_url"] for row in listed.json()] == ["urn:songhive:user:other"]

    profile = client.get("/api/v1/users/other", headers=headers).json()
    assert profile["muted"] is True
    assert profile["blocked"] is False

    resp = client.request("DELETE", "/api/v1/users/me/mutes", json={"actor_url": "other"}, headers=headers)
    assert resp.status_code == 204
    profile = client.get("/api/v1/users/other", headers=headers).json()
    assert profile["muted"] is False


def test_block_api_roundtrip_and_self_guard(client, regular_user, other_user, auth_headers):
    """Block via API, confirm the flag and list, reject self-blocks."""
    headers = auth_headers(regular_user)
    resp = client.post("/api/v1/users/me/blocks", json={"actor_url": "regular"}, headers=headers)
    assert resp.status_code == 400

    resp = client.post("/api/v1/users/me/blocks", json={"actor_url": "other"}, headers=headers)
    assert resp.status_code == 201, resp.text
    listed = client.get("/api/v1/users/me/blocks", headers=headers)
    assert [row["actor_url"] for row in listed.json()] == ["urn:songhive:user:other"]

    profile = client.get("/api/v1/users/other", headers=headers).json()
    assert profile["blocked"] is True

    resp = client.request("DELETE", "/api/v1/users/me/blocks", json={"actor_url": "other"}, headers=headers)
    assert resp.status_code == 204
    profile = client.get("/api/v1/users/other", headers=headers).json()
    assert profile["blocked"] is False


def test_mutes_require_auth(client):
    resp = client.get("/api/v1/users/me/mutes")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# API: /admin/moderation/*
# ---------------------------------------------------------------------------


def test_admin_limit_and_suspend_api(client, admin_user, regular_user, other_user, auth_headers):
    """Admin limit/suspend lifecycle through the moderation endpoints."""
    headers = auth_headers(admin_user)

    resp = client.post(
        "/api/v1/admin/moderation/users",
        json={"actor_url": "other", "action": "limit", "reason": "suspect"},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["action"] == "limit"
    assert body["reason"] == "suspect"
    assert body["local_username"] == "other"

    listed = client.get("/api/v1/admin/moderation/users?action=limit", headers=headers).json()
    assert [row["actor_url"] for row in listed] == ["urn:songhive:user:other"]

    profile = client.get("/api/v1/users/other", headers=headers).json()
    assert profile["limited"] is True
    assert profile["suspended"] is False

    # Escalate to suspend — the same row updates.
    resp = client.post(
        "/api/v1/admin/moderation/users",
        json={"actor_url": "other", "action": "suspend", "reason": "confirmed"},
        headers=headers,
    )
    assert resp.status_code == 201
    profile = client.get("/api/v1/users/other", headers=headers).json()
    assert profile["suspended"] is True

    suspended = client.get("/api/v1/admin/moderation/users?action=suspend", headers=headers).json()
    assert len(suspended) == 1
    assert suspended[0]["reason"] == "confirmed"
    assert client.get("/api/v1/admin/moderation/users?action=limit", headers=headers).json() == []

    resp = client.request(
        "DELETE",
        "/api/v1/admin/moderation/users",
        json={"actor_url": "other"},
        headers=headers,
    )
    assert resp.status_code == 204
    profile = client.get("/api/v1/users/other", headers=headers).json()
    assert profile["suspended"] is False


def test_admin_moderation_requires_admin(client, regular_user, other_user, auth_headers):
    headers = auth_headers(regular_user)
    resp = client.post(
        "/api/v1/admin/moderation/users",
        json={"actor_url": "other", "action": "limit"},
        headers=headers,
    )
    assert resp.status_code == 403
    resp = client.get("/api/v1/admin/moderation/users", headers=headers)
    assert resp.status_code == 403


def test_admin_cannot_moderate_self(client, admin_user, auth_headers):
    resp = client.post(
        "/api/v1/admin/moderation/users",
        json={"actor_url": "admin", "action": "suspend"},
        headers=auth_headers(admin_user),
    )
    assert resp.status_code == 400


def test_admin_instance_moderation_api(client, admin_user, auth_headers):
    """Defederate/followers-only lifecycle plus the domain_blocks endpoint."""
    headers = auth_headers(admin_user)

    resp = client.post(
        "/api/v1/admin/moderation/instances",
        json={"domain": "bad.example", "action": "defederate", "reason": "spam farm"},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["domain"] == "bad.example"

    resp = client.post(
        "/api/v1/admin/moderation/instances",
        json={"domain": "quiet.example", "action": "followers_only"},
        headers=headers,
    )
    assert resp.status_code == 201

    listed = client.get("/api/v1/admin/moderation/instances?action=defederate", headers=headers).json()
    assert [row["domain"] for row in listed] == ["bad.example"]
    assert listed[0]["reason"] == "spam farm"

    # Mastodon-style domain_blocks transparency endpoint.
    blocks = client.get("/api/v1/instance/domain_blocks").json()
    by_domain = {row["domain"]: row for row in blocks}
    assert by_domain["bad.example"]["severity"] == "suspend"
    assert by_domain["bad.example"]["comment"] == "spam farm"
    assert by_domain["quiet.example"]["severity"] == "silence"

    resp = client.request(
        "DELETE",
        "/api/v1/admin/moderation/instances",
        json={"domain": "bad.example"},
        headers=headers,
    )
    assert resp.status_code == 204
    blocks = client.get("/api/v1/instance/domain_blocks").json()
    assert "bad.example" not in {row["domain"] for row in blocks}


async def test_admin_moderation_audit_log(client, db_session, admin_user, other_user, auth_headers):
    """Admin moderation actions record ``domain.verb`` audit entries."""
    headers = auth_headers(admin_user)
    resp = client.post(
        "/api/v1/admin/moderation/users",
        json={"actor_url": "other", "action": "suspend", "reason": "abuse"},
        headers=headers,
    )
    assert resp.status_code == 201

    logs = (await db_session.execute(select(AuditLog).order_by(AuditLog.created_at))).scalars().all()
    entry = [row for row in logs if row.action == "moderation.user_suspend"]
    assert len(entry) == 1
    assert entry[0].actor_id == admin_user.id
    assert entry[0].target_type == "actor"
    assert entry[0].target_id == "urn:songhive:user:other"
    assert entry[0].details["reason"] == "abuse"


def test_admin_instance_rejects_bad_input(client, admin_user, auth_headers):
    headers = auth_headers(admin_user)
    resp = client.post(
        "/api/v1/admin/moderation/instances",
        json={"domain": "x.example", "action": "explode"},
        headers=headers,
    )
    assert resp.status_code == 422
