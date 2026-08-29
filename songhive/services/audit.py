"""
Audit log service.

Provides helpers to create and query ``AuditLog`` rows from the rest of the
application. The list endpoint also enriches each record with human-readable
names for the actor and target, making admin views more scannable.
"""

import asyncio
from collections import defaultdict
from typing import Any, List, Optional, Tuple

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.album import Album
from ..models.artist import Artist
from ..models.audit_log import AuditLog
from ..models.hashtag import Hashtag
from ..models.invite import Invite
from ..models.library import Library
from ..models.oauth_client import OAuth2Client
from ..models.playlist import Playlist
from ..models.report import Report
from ..models.stored_file import StoredFile
from ..models.track import Track
from ..models.user import User


async def log_action(
    session: AsyncSession,
    *,
    actor_id: Optional[str],
    action: str,
    target_type: Optional[str] = None,
    target_id: Optional[str] = None,
    details: Optional[dict] = None,
    ip_address: Optional[str] = None,
) -> AuditLog:
    """Create and flush a single audit log entry."""
    log = AuditLog(
        actor_id=actor_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        details=details,
        ip_address=ip_address,
    )
    session.add(log)
    await session.flush()
    return log


def _apply_filters(
    stmt: Select[Any],
    *,
    action: Optional[str] = None,
    actor_id: Optional[str] = None,
    target_type: Optional[str] = None,
    target_id: Optional[str] = None,
) -> Select[Any]:
    """Apply audit log filters to a statement."""
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if actor_id:
        stmt = stmt.where(AuditLog.actor_id == actor_id)
    if target_type:
        stmt = stmt.where(AuditLog.target_type == target_type)
    if target_id:
        stmt = stmt.where(AuditLog.target_id == target_id)
    return stmt


def _build_list_stmt(
    action: Optional[str] = None,
    actor_id: Optional[str] = None,
    target_type: Optional[str] = None,
    target_id: Optional[str] = None,
) -> Select[Any]:
    """Build a filtered, ordered statement for audit log rows."""
    stmt = select(AuditLog).order_by(AuditLog.created_at.desc())
    return _apply_filters(
        stmt,
        action=action,
        actor_id=actor_id,
        target_type=target_type,
        target_id=target_id,
    )


async def list_audit_logs(
    session: AsyncSession,
    *,
    action: Optional[str] = None,
    actor_id: Optional[str] = None,
    target_type: Optional[str] = None,
    target_id: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[AuditLog], int]:
    """Return a paginated list of audit logs and the total matching count."""
    stmt = _build_list_stmt(
        action=action,
        actor_id=actor_id,
        target_type=target_type,
        target_id=target_id,
    )
    total = await count_audit_logs(
        session,
        action=action,
        actor_id=actor_id,
        target_type=target_type,
        target_id=target_id,
    )
    stmt = stmt.offset(offset).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all()), total


async def count_audit_logs(
    session: AsyncSession,
    *,
    action: Optional[str] = None,
    actor_id: Optional[str] = None,
    target_type: Optional[str] = None,
    target_id: Optional[str] = None,
) -> int:
    """Return the total number of audit logs matching the filters."""
    stmt = _apply_filters(
        select(func.count(AuditLog.id)),
        action=action,
        actor_id=actor_id,
        target_type=target_type,
        target_id=target_id,
    )
    result = await session.execute(stmt)
    return result.scalar() or 0


# Mapping of target type to (model, [candidate name columns], [detail fallback keys])
# For the "user" target type, the first candidate is the display name and the second
# is the username. The username is also exposed separately for router links.
_TARGET_NAME_FIELDS: dict[str, Tuple[Any, List[str], List[str]]] = {
    "album": (Album, ["title"], ["title"]),
    "artist": (Artist, ["name"], ["name"]),
    "file": (StoredFile, ["original_filename"], ["original_filename"]),
    "hashtag": (Hashtag, ["name"], ["name"]),
    "invite": (Invite, ["code"], ["code"]),
    "library": (Library, ["name"], ["name"]),
    "oauth_client": (OAuth2Client, ["name", "client_id"], ["name", "client_id"]),
    "playlist": (Playlist, ["name"], ["name"]),
    "report": (Report, [], []),
    "track": (Track, ["title"], ["title"]),
    "user": (User, ["display_name", "username"], ["username", "display_name"]),
}


def _first_non_null(values: List[Optional[str]]) -> Optional[str]:
    """Return the first truthy string from a list, or None."""
    for value in values:
        if value:
            return str(value)
    return None


def _name_from_details(details: Optional[dict], keys: List[str]) -> Optional[str]:
    """Try to find a human-readable name in the details dict."""
    if not details or not isinstance(details, dict):
        return None
    for key in keys:
        value = details.get(key)
        if value is not None and value != "":
            return str(value)
    return None


async def _load_user_info(
    session: AsyncSession,
    user_ids: set[str],
) -> dict[str, dict[str, Optional[str]]]:
    """Load username and display_name for a set of user ids."""
    if not user_ids:
        return {}
    result = await session.execute(select(User.id, User.username, User.display_name).where(User.id.in_(user_ids)))
    return {
        str(row.id): {
            "username": row.username,
            "display_name": row.display_name,
        }
        for row in result.all()
    }


async def _load_target_name_map(
    session: AsyncSession,
    target_type: str,
    target_ids: set[str],
) -> dict[str, str]:
    """Load a map of target_id -> name for a single target type."""
    if not target_ids:
        return {}

    fields = _TARGET_NAME_FIELDS.get(target_type)
    if not fields or not fields[1]:
        return {}

    model, columns, _ = fields
    stmt = select(
        model.id,
        *(getattr(model, c) for c in columns),
    ).where(model.id.in_(target_ids))
    result = await session.execute(stmt)

    name_map: dict[str, str] = {}
    for row in result.all():
        row_dict = row._asdict()
        target_id = str(row_dict["id"])
        name = _first_non_null([row_dict.get(c) for c in columns])
        if name:
            name_map[target_id] = name
    return name_map


async def _load_all_target_names(
    session: AsyncSession,
    logs: list[AuditLog],
) -> dict[str, dict[str, str]]:
    """Load names for all non-user targets referenced in the logs."""
    targets_by_type: dict[str, set[str]] = defaultdict(set)
    for log in logs:
        if log.target_id and log.target_type and log.target_type != "user":
            targets_by_type[log.target_type].add(log.target_id)

    results = await asyncio.gather(
        *(
            _load_target_name_map(session, target_type, target_ids)
            for target_type, target_ids in targets_by_type.items()
        )
    )
    return dict(zip(targets_by_type.keys(), results))


def _log_dict(log: AuditLog) -> dict[str, Any]:
    """Return a plain dict of the audit log's column values."""
    return {column.name: getattr(log, column.name) for column in log.__table__.columns}


async def enrich_audit_logs(
    session: AsyncSession,
    logs: list[AuditLog],
) -> list[dict[str, Any]]:
    """
    Enrich audit logs with human-readable actor/target names and usernames.

    Returns a list of dicts suitable for ``AuditLogResponse.model_validate``.
    """
    user_ids: set[str] = set()
    for log in logs:
        if log.actor_id:
            user_ids.add(log.actor_id)
        if log.target_id and log.target_type == "user":
            user_ids.add(log.target_id)

    user_info = await _load_user_info(session, user_ids)
    target_names = await _load_all_target_names(session, logs)

    enriched: list[dict[str, Any]] = []
    for log in logs:
        data = _log_dict(log)

        actor_name: Optional[str] = None
        actor_username: Optional[str] = None
        if log.actor_id and log.actor_id in user_info:
            info = user_info[log.actor_id]
            actor_username = info["username"]
            actor_name = _first_non_null([info["display_name"], info["username"]])

        target_name: Optional[str] = None
        target_username: Optional[str] = None
        if log.target_id and log.target_type:
            if log.target_type == "user":
                if log.target_id in user_info:
                    info = user_info[log.target_id]
                    target_username = info["username"]
                    target_name = _first_non_null([info["display_name"], info["username"]])
                if target_username is None:
                    target_username = _name_from_details(log.details, ["username", "display_name"])
                    target_name = target_username
            else:
                type_map = target_names.get(log.target_type, {})
                target_name = type_map.get(log.target_id)

            if target_name is None:
                fields = _TARGET_NAME_FIELDS.get(log.target_type)
                fallback_keys = fields[2] if fields else []
                target_name = _name_from_details(log.details, fallback_keys)

        data["actor_name"] = actor_name
        data["actor_username"] = actor_username
        data["target_name"] = target_name
        data["target_username"] = target_username
        enriched.append(data)

    return enriched
