"""
Actor management for federation.
Each Songhive user has a corresponding ActivityPub actor.
"""

from ..models.user import User


def get_actor_url(domain: str, username: str) -> str:
    """Get the ActivityPub actor URL for a user."""
    return f"https://{domain}/users/{username}"


def get_inbox_url(domain: str, username: str) -> str:
    """Get the inbox URL for a user."""
    return f"https://{domain}/users/{username}/inbox"


def get_outbox_url(domain: str, username: str) -> str:
    """Get the outbox URL for a user."""
    return f"https://{domain}/users/{username}/outbox"


def user_to_actor_document(user: User, domain: str) -> dict:
    """
    Convert a User model to an ActivityPub actor document.
    """
    actor_url = get_actor_url(domain, user.username)
    return {
        "@context": [
            "https://www.w3.org/ns/activitystreams",
            "https://w3id.org/security/v1",
        ],
        "id": actor_url,
        "type": "Person",
        "preferredUsername": user.username,
        "name": user.display_name or user.username,
        "summary": user.bio or "",
        "inbox": get_inbox_url(domain, user.username),
        "outbox": get_outbox_url(domain, user.username),
        "followers": f"{actor_url}/followers",
        "following": f"{actor_url}/following",
        "publicKey": {
            "id": f"{actor_url}#main-key",
            "owner": actor_url,
            "publicKeyPem": user.public_key_pem or "",
        },
    }
