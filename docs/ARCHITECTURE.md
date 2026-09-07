# Songhive Architecture

## System Overview

Songhive is a federated and self-hosted music sharing service. It uses
ActivityPub for federation (via [pubby](https://github.com/blacklight/pubby))
and is designed to interoperate with Mastodon-compatible clients and the
fediverse at large.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              External Services                              │
│  ┌──────────────┐  ┌────────────────┐  ┌─────────────────────────────────┐  │
│  │ MusicBrainz  │  │ Cover Art      │  │ Federated Instances (fediverse) │  │
│  │ (metadata +  │  │ Archive (cover │  │ (ActivityPub inbox/outbox)      │  │
│  │  MBID lookup)│  │  art images)   │  └─────────────────────────────────┘  │
│  └──────────────┘  └────────────────┘                                       │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                          Reverse Proxy (Nginx)                              │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                    ┌─────────────────┼─────────────────┐
                    ▼                 ▼                 ▼
┌─────────────────────────┐ ┌─────────────────┐ ┌─────────────────────────────┐
│    Frontend (Vue.js 3)  │ │  Tornado Server │ │   Static/Media Files        │
│    TypeScript + Vite    │ │  (FastAPI ASGI) │ │   (Local filesystem / S3)   │
└─────────────────────────┘ └─────────────────┘ └─────────────────────────────┘
                                      │
              ┌───────────────────────┼──────────────────────┐
              ▼                       ▼                      ▼
      ┌─────────────┐         ┌─────────────┐        ┌─────────────┐
      │  PostgreSQL │         │    Redis    │        │   Celery    │
      │  (Primary   │         │  (Cache /   │        │  (Workers)  │
      │   Database) │         │   Broker /  │        │             │
      │             │         │   Sessions) │        │             │
      └─────────────┘         └─────────────┘        └─────────────┘
```

---

## Backend Stack

| Component      | Technology                                         |
|----------------|----------------------------------------------------|
| API Framework  | FastAPI (ASGI)                                    |
| Server         | Tornado (wrapping FastAPI via `a2wsgi`)            |
| Fallback server| uvicorn (when `a2wsgi` is unavailable)             |
| WebSockets     | Tornado native WebSocket handlers                 |
| Data Models    | Pydantic v2 (validation/serialization)            |
| ORM            | SQLAlchemy 2 (async, mapped columns)              |
| Task Queue     | Celery + Redis                                    |
| Cache/Sessions | Redis                                             |
| Federation     | pubby (ActivityPub library)                       |
| Auth           | JWT access tokens + opaque refresh tokens (Redis) |
| OAuth2         | authlib (OAuth2 provider for third-party apps)    |
| Email          | SMTP (via Python `smtplib`)                       |
| Metadata       | mutagen (tag reading), MusicBrainz API            |
| Transcoding    | ffmpeg (system dependency)                        |
| Rate limiting  | Redis sliding-window (per-IP and per-user)        |

---

## Application Structure

```
songhive/
├── __init__.py
├── __main__.py
├── app.py                  # Entry point: Tornado+FastAPI bootstrap; uvicorn fallback
├── version.py
├── cli/                    # Admin commands (init-db, migrate, create-user, etc.)
│   └── admin.py
├── config/                 # Configuration management
│   ├── schema.py           # Pydantic BaseSettings model (all subsections)
│   └── loader.py           # TOML + env vars + CLI argument loading
├── api/                    # FastAPI application
│   ├── app.py              # FastAPI factory: middleware, route registration, federation setup
│   ├── _common.py          # Shared helpers (pagination, client IP, etc.)
│   ├── deps.py             # Dependency injection (DB session, current user, config, Redis, storage)
│   ├── errors.py           # RFC 7807 problem-detail exception handlers
│   ├── routes/             # Route modules (one file per resource)
│   │   ├── auth.py         # Login, registration, token refresh, password reset
│   │   ├── sessions.py     # List and revoke active refresh-token sessions
│   │   ├── users.py        # User profiles, avatar, links, password change
│   │   ├── activities.py   # Activity interactions (like, …)
│   │   ├── artists.py
│   │   ├── albums.py
│   │   ├── tracks.py
│   │   ├── playlists.py
│   │   ├── libraries.py
│   │   ├── favorites.py
│   │   ├── hashtags.py     # Global hashtag browsing and admin deletion
│   │   ├── genres.py       # Global genre browsing and admin deletion
│   │   ├── history.py      # Listening history
│   │   ├── radios.py       # Dynamic radio generation
│   │   ├── files.py        # Generic file upload/download (StoredFile)
│   │   ├── shares.py       # Share grants (owner→specific user)
│   │   ├── share_urls.py   # Share URL tokens (revocable short links)
│   │   ├── share.py        # Public token resolver (redirects + sets cookie)
│   │   ├── reports.py      # Content moderation reports + admin review
│   │   ├── federation.py   # Per-user ActivityPub actors + WebFinger
│   │   ├── admin.py        # Admin endpoints (settings, stats, user management)
│   │   ├── external_libraries.py # User external library CRUD, sync, tracks
│   │   └── admin_external_libraries.py # Admin external library management
│   └── middleware/
│       ├── auth.py         # JWT decode middleware + access-token helpers
│       └── rate_limit.py   # Redis sliding-window rate limiting (IP / user)
├── migrations/             # Alembic database migrations
│   ├── env.py              # Migration environment (imports all models)
│   ├── script.py.mako      # Template for generated revisions
│   └── versions/           # Revision scripts
├── models/                 # SQLAlchemy mapped models + shared enums
│   ├── base.py             # DeclarativeBase, UUID PK, timestamps, async session factory
│   ├── _enums.py           # Visibility enum (private / mentioned / local / followers / public)
│   ├── activity.py         # Activity, ActivityMention, ActivityTarget (federation interaction layer)
│   ├── user.py             # User (roles: user / moderator / admin; federation fields)
│   ├── user_link.py        # Profile links (validated URL list)
│   ├── invite.py           # Invite codes (max_uses, expiry)
│   ├── artist.py
│   ├── album.py
│   ├── track.py
│   ├── upload.py           # Raw uploaded file reference
│   ├── stored_file.py      # Content-addressable file (SHA-256, visibility, owner)
│   ├── transcoded_file.py  # Transcode cache: (track_id, format, bitrate) → StoredFile
│   ├── library.py
│   ├── library_track.py    # Library ↔ Track join table
│   ├── playlist.py
│   ├── favorite.py
│   ├── genre.py            # Genre and GenreTrack/GenreAlbum associations
│   ├── history.py          # Listening history entries
│   ├── radio.py
│   ├── share_grant.py      # Per-user access grant for a specific item
│   ├── share_token.py      # Revocable short-link token (stores SHA-256 hash only)
│   ├── report.py           # Content moderation report
│   ├── oauth_client.py     # Registered OAuth2 clients
│   ├── audit_log.py        # Admin/security audit trail
│   ├── external_library.py # External library adapter instance
│   ├── external_sync_run.py # External library sync history
│   ├── external_track.py   # Track discovered through an external provider
│   └── setting.py          # Runtime-editable instance settings (key/JSON-value)
├── services/               # Business logic layer
│   ├── acl.py              # Three-level visibility + share-grant + share-token ACL
│   ├── activities.py       # Activity domain service (entity resolution, creation, interactions)
│   ├── auth.py             # User lookup, password hashing, session helpers
│   ├── audit.py            # Audit log helpers
│   ├── deletion.py         # Cascade deletion + activity retraction fan-out
│   ├── email.py            # SMTP email (verification, password reset)
│   ├── federation.py       # Actor provisioning, domain allow/block, inbox dispatch
│   ├── genres.py           # Genre validation, association and listing
│   ├── import_.py          # Import pipeline orchestration
│   ├── mentions.py         # @handle extraction, local/WebFinger resolution, safe HTML rendering
│   ├── metadata.py         # Tag extraction coordination
│   ├── music.py            # Music library helpers
│   ├── musicbrainz.py      # MusicBrainz + Cover Art Archive enrichment (async httpx)
│   ├── redis.py            # Redis client lifecycle
│   ├── reports.py          # Content report CRUD
│   ├── secrets.py          # Config encryption, decryption, and secret redaction
│   ├── settings.py         # Instance settings with Redis cache + config overlay
│   ├── sharing.py          # Share-grant and share-token CRUD
│   ├── stats.py            # Admin dashboard statistics
│   ├── storage.py          # StorageService facade (delegates to storage backend)
│   └── streaming.py        # Track file resolution, transcode cache, history recording
├── external/               # External library adapter interface and registry
│   ├── base.py             # BaseExternalLibraryAdapter protocol
│   ├── _fake.py            # In-memory fake adapter for tests
│   ├── _local.py           # Local filesystem adapter
│   ├── watchdog.py         # Filesystem watchdog for local libraries
│   ├── registry.py         # Provider-type registry
│   └── types.py            # Adapter dataclasses (ItemRef, TrackMetadata, etc.)
├── federation/             # ActivityPub per-user federation
│   ├── _common.py          # URL builders
│   ├── actors.py           # Actor document generation, federation storage helpers
│   ├── activities.py       # Activity creation (Create, Update, Delete, etc.)
│   ├── serializers.py      # Track → ActivityPub Audio object mapping
│   └── storage.py          # pubby storage adapter (SQLAlchemy-backed)
├── users/                  # User management
│   ├── manager.py          # User CRUD, password management
│   ├── invites.py          # Invite-code creation, validation, consumption
│   ├── oauth.py            # OAuth2 provider setup (authlib)
│   └── tokens.py           # JWT access + opaque refresh token issuance/rotation/revocation, session listing
├── music/                  # Music domain logic
│   ├── importer.py         # File importer: save → extract tags → link track
│   └── metadata.py         # Tag reading (mutagen), field normalization
├── streaming/              # Audio streaming
│   ├── handler.py          # Tornado streaming handler (range requests, send-file)
│   └── transcoder.py       # ffmpeg wrapper: MP3, OGG, FLAC, AAC, Opus
├── storage/                # Media storage backends
│   ├── base.py             # Abstract StorageBackend interface + FileSizeLimitExceededError
│   ├── exc.py              # Storage-layer exceptions
│   ├── local.py            # Local filesystem backend
│   └── s3.py               # S3-compatible object storage backend
├── tasks/                  # Celery task definitions
│   ├── celery.py           # Celery app factory (crontab parser, config loading)
│   ├── import_.py          # Import pipeline tasks
│   ├── federation.py       # Activity delivery + inbox processing tasks
│   ├── transcoding.py      # Pre-transcoding tasks
│   ├── email.py            # Email delivery tasks
│   ├── musicbrainz.py      # MusicBrainz metadata + Cover Art Archive enrichment
│   ├── images.py           # Artist image + album cover enrichment
│   ├── external_libraries.py # External library sync task
│   └── storage.py          # Orphaned-file cleanup (scheduled via crontab)
├── ws/                     # WebSocket support
│   └── events.py           # Tornado WebSocket handler (JWT auth, CORS origin check)
└── cli/                    # CLI commands
    └── admin.py            # Admin commands (create-user, provision-federation-keys, etc.)
```

---

## Server Architecture: Tornado + FastAPI

The server uses Tornado as the top-level HTTP server. The FastAPI ASGI app is
bridged via `a2wsgi` (ASGI→WSGI) and wrapped in Tornado's `WSGIContainer`.
When `a2wsgi` is not installed the server falls back to `uvicorn` (pure ASGI,
loses native Tornado handlers).

**Why Tornado as the outer server:**

- **Native WebSockets** — the real-time events endpoint is a proper Tornado
  `WebSocketHandler`, avoiding ASGI WebSocket complexity.
- **Streaming handler** — audio files are streamed via a dedicated Tornado
  handler with native range-request support.
- **Signal handling / graceful shutdown** — Tornado's `IOLoop` controls the
  process lifecycle.

```python
# Simplified bootstrap (songhive/app.py)
from a2wsgi import ASGIMiddleware
from tornado.web import Application, FallbackHandler
from tornado.wsgi import WSGIContainer
from tornado.httpserver import HTTPServer

from songhive.api.app import create_app
from songhive.ws.events import EventWebSocket
from songhive.streaming.handler import StreamHandler

fastapi_app = create_app(config)
wsgi_app = ASGIMiddleware(fastapi_app)
container = WSGIContainer(wsgi_app)

tornado_app = Application([
    (r"/ws/events", EventWebSocket),
    (r"/ws/", EventWebSocket),
    (r"/api/v1/stream/(?P<track_id>[^/]+)", StreamHandler),
    (r".*", FallbackHandler, {"fallback": container}),
])

server = HTTPServer(tornado_app)
server.listen(config.server.port)
```

---

## Configuration

Configuration is loaded with the following priority (highest first):

1. Environment variables (prefixed `SONGHIVE_`, nested with `__`)
2. CLI arguments
3. `config.toml` (searched at the path given by `--config` or `SONGHIVE_CONFIG`,
   then `./config.toml`, then `$XDG_CONFIG_HOME/songhive/config.toml` or
   `~/.config/songhive/config.toml`, and finally `/etc/songhive/config.toml`)
4. Field defaults

The root schema is `SonghiveConfig` (a Pydantic `BaseSettings`), composed of
these subsections:

| Section        | Key settings                                                  |
|----------------|---------------------------------------------------------------|
| `server`       | host, port, num_workers, debug, cors_origins                  |
| `database`     | url (asyncpg), pool_size, max_overflow                        |
| `redis`        | url                                                           |
| `celery`       | broker_url, result_backend, cleanup_orphaned_files_schedule   |
| `storage`      | backend (local/s3), local_path, s3_*, cdn_prefix, max_upload_size |
| `federation`   | enabled, instance_domain, instance_name, private_key_path, allow/block lists |
| `auth`         | registration_mode, secret_key, token TTLs, rate_limit, trusted_proxy_hops |
| `email`        | smtp_host, smtp_port, smtp_user, from_address, tls settings   |
| `musicbrainz`  | enabled, user_agent, cover_art, artist_image settings       |
| `imports`      | scan_roots, bulk_import_sync_threshold                        |
| `streaming`    | max_bitrate, max_bitrate_by_role, default_bitrate, chunk_size, transcode_cache_enabled |

Runtime-editable overrides (instance settings stored in the `settings` DB
table, cached in Redis) are applied over the file-based config at startup and
can be changed via the admin API without a restart.

---

## Data Model (Core Entities)

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐     ┌──────────────┐
│   Artist    │────▶│    Album    │────▶│    Track    │────▶│  StoredFile  │
└─────────────┘     └─────────────┘     └─────────────┘     │ (audio_file) │
                                               │            └──────────────┘
                                               │
                                               ▼
                                        ┌─────────────┐     ┌──────────────┐
                                        │   Upload    │────▶│  StoredFile  │
                                        │ (raw upload)│     │ (upload file)│
                                        └─────────────┘     └──────────────┘
                                               │
                                               ▼
                                        ┌──────────────────┐
                                        │ TranscodedFile   │
                                        │ (track, format,  │
                                        │  bitrate)        │
                                        └──────────────────┘

┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│    User     │────▶│  Library    │────▶│ LibraryTrack│
│  (roles:    │     └─────────────┘     └─────────────┘
│  user /     │
│  moderator /│     ┌─────────────┐     ┌─────────────┐
│  admin)     │────▶│  Playlist   │────▶│  Track      │
│             │     └─────────────┘     └─────────────┘
│             │
│             │     ┌─────────────┐     ┌─────────────┐
│             │────▶│ ShareGrant  │────▶│  (any item) │
│             │     └─────────────┘     └─────────────┘
│             │
│             │     ┌─────────────┐     ┌─────────────┐
│             │────▶│ ShareToken  │────▶│  (any item) │
│             │     └─────────────┘     └─────────────┘
│             │
│             │     ┌─────────────┐     ┌─────────────┐
│             │────▶│   Invite    │     │  UserLink   │
│             │     └─────────────┘     └─────────────┘
└─────────────┘
```

**Visibility levels** (`Visibility` enum) are ordered from most to least
restrictive: `private < mentioned < local < followers < public`
(`Visibility.rank`, `Visibility.can_contain`). Entity visibility currently
uses `private`, `local`, and `public` (applies to tracks, albums, artists,
libraries, stored files):

- `private` — visible only to the owner (and users with a `ShareGrant`)
- `local` — visible to authenticated users on the same instance
- `public` — visible to everyone including federated instances

The `mentioned` and `followers` levels exist for the activities layer
(`Activity.visibility`) and are not yet meaningful for entity ACLs.

### Activities

`Activity` records federation-relevant events (`create`, `announce`, `like`,
`reply`, `quote`, `mention`, `update`, `delete`, `webmention`) attached to an
entity through `(entity_type, entity_id)` — where `entity_type` is one of
`track`, `album`, `artist`, `playlist`, `library`. Each row tracks its origin
via `source_type` (`local` or a remote source), `source_actor`, and
`source_id` (unique per source), with `local_object_id` as an optional
canonical local identifier. Activities support threading through
`in_reply_to_activity_id`, arbitrary JSON `payload`s, Markdown source vs
rendered content (`content_source` / `content` / `content_type`), and soft
deletion (`deleted_at`, `retracted`). An activity's `visibility` must not
exceed its parent entity's visibility (`Visibility.can_contain`).

`ActivityMention` rows capture `@handle` mentions embedded in content, with
optional `actor_url` / `user_id` resolution and a `notified_at` marker.
`services/mentions.py` implements the mention pipeline: `MENTION_REGEX`
extracts `@user` and `@user@domain` handles, `resolve_mentions` resolves bare
handles against the local `users` table (case-insensitive, active users only)
and remote handles through `pubby.resolve_actor_url` (run in a thread, with
Pubby's `https://{domain}/@{username}` fallback on lookup failure). Remote
resolution is gated on `federation.enabled`, requires a dotted domain, and
drops handles on blocked or non-allowed instances via
`services/federation.is_domain_blocked`; `@user@domain` handles naming the
local instance resolve locally instead. `render_mentions` builds safe HTML —
resolved handles become anchors via `pubby.render_link_anchor`, surrounding
text is escaped and linkified by `pubby.render_post_html` — and
`process_mentions` is the single entry point returning resolved mentions,
rendered HTML, hashtags, and ActivityPub `Mention`/`Hashtag` tags.
`ActivityTarget` rows track per-inbox outbound delivery state (`pending`,
`sent`, `failed`, `skipped`) with `attempts` / `last_error` /
`last_attempt_at` bookkeeping. Tracks already published to the fediverse
(`federation_object_id` set) are backfilled as `create` activities by
migration `4adb5fbea9d6`.

`services/activities.py` is the domain entry point: `resolve_entity` maps an
`(entity_type, entity_id)` pair to its model row, and
`create_local_activity` validates the entity (404), enforces
`Visibility.can_contain` against the entity (422 — entities without a
visibility column, currently `Artist`, act as public containers), checks
`acl.can_manage` (403), then persists the activity plus any pre-resolved
`ActivityMention` rows. Local source identity reuses the author's
`actor_url`, falling back to a `urn:songhive:user:{username}` URN when the
user has no provisioned federation identity; `source_id` embeds the same
UUID as `local_object_id` (`{actor}/objects/{uuid}`) so section-9 object
routes can resolve it.

`can_view_activity` decides who may see an activity: the containing entity
must pass `acl.can_access`, and the activity's own `visibility` applies on
top (`public` follows the entity check; `local`/`followers` require an
authenticated user; `mentioned` requires the owner, an admin, or a mentioned
user; `private` is owner/admin-only; retracted activities are never
viewable). Interactions are layered on top of `create_local_activity`:
`like_activity` records an idempotent `like` (400 on a duplicate, 404 on a
retracted target) that inherits the target's visibility and stores a
`federation/activities.create_like_activity` `Like` payload whose `to`/`cc`
come from `activity_audience`. Interactions skip the `can_manage` gate
(`require_manage=False`) — view access on the target is enough — while
content-producing activity types still require manage rights. The
`POST /api/v1/activities/{id}/like` endpoint performs the 404/403/400
checks, provisions the liker's actor keys (`ensure_user_actor`), commits,
then calls `services/federation.publish_like_activity` in a thread: for
remote targets it resolves the author's inbox via `resolve_actor_inbox`
(the `federation_actor_cache` table first, then a signed actor-document
fetch, preferring `sharedInbox`) and enqueues the real
`tasks.federation.deliver_activity` Celery task — which applies the
federation gate, the blocked-domain gate, and exponential-backoff retries.

`VisibilityRules` centralizes the containment policy: `can_contain` and
`enforce_activity_visibility` (used by `create_local_activity`) validate an
activity's visibility against its entity's, while
`cascade_visibility_update` changes a local activity's visibility and fans
the change out to the inboxes recorded as `sent` in `activity_targets`.
When the new visibility still federates (`Visibility.federates`:
`mentioned`, `followers`, `public`) those inboxes receive an `Update`
carrying the new `to`/`cc` audience built by
`federation.activities.create_visibility_update_activity` on top of
`activity_audience`; when it does not (`private`, `local`) a
`Delete(Tombstone)` retracts the object instead. Remote activities and
soft-deleted local activities never fan out, and deliveries are signed with
the activity owner's key through `deletion.enqueue_activity_delivery`.

Deletion is handled by `services/deletion.py`'s `cascade_delete_entity`,
which is invoked from every entity delete path (track, album, artist,
playlist, library). Local activities are soft-deleted (`deleted_at` set)
and a `Delete(Tombstone)` is enqueued through `deliver_activity` for every
inbox recorded as `sent` in `activity_targets`, signed with the activity
owner's private key; remote activities are hard-deleted without fan-out.
`get_activity_unpublish_info` returns the `ActivityUnpublishInfo`
(activity id, `source_id`, `actor_url`, sent inboxes) used for that
delivery, and `federation/activities.py` builds the tombstone payload via
`create_tombstone_delete_activity`.

### Genres

Genres are stored in a dedicated `Genre` table and linked to `Track` and
`Album` through `GenreTrack` and `GenreAlbum` association tables. The free-text
`Track.genre` and `Album.genre` columns remain the source of truth for embedded
metadata round-trips, while the normalised tables enable browsing, counting, and
filtering. `GenreTrack.inherited` distinguishes album-inherited values from
explicit track-level overrides. `services/genres.py` validates names, manages
associations, propagates album genres down to tracks that have no explicit
genre of their own, and re-derives the album genre from the intersection of its
tracks' explicit genres. The hashtag system receives the same genre-derived
tags: the genre string is split and mapped to valid hashtag names so
genre-derived hashtags appear alongside user-created ones. The public API
exposes global genre listing and deletion in `api/routes/genres.py`, and
per-resource genre management is supported through `POST`/`DELETE` sub-routes on
tracks and albums as well as the `genre` field on track/album updates. The
frontend mirrors the hashtag browsing experience: `GenresView` and `GenreView`
list and filter genres, `GenreInput`/`GenreList` let users edit and display
genres on tracks and albums, and the sidebar provides a top-level "Genres"
navigation link.

---

## External Libraries

Songhive supports attaching external music libraries (e.g., cloud storage
adapters) to user- or admin-owned Songhive libraries. The feature is built
around a pluggable adapter model in `songhive/external/`, with a per-provider
registry (`songhive/external/registry.py`) and a `BaseExternalLibraryAdapter`
interface.

Adapters live in child plans and are registered at import time. Each adapter
exposes capabilities (`list_items`, `read_metadata`, `open_stream`, `download`,
`write_tags`, `rename_source`, `delete_source`, `compute_hash`) and a sanitized
configuration schema. Provider-specific implementations are kept outside the
core codebase and register through `songhive.external.registry`.

Models:

- `ExternalLibrary` — adapter instance, encrypted provider config, capabilities,
  and scope (`user` or `admin`).
- `ExternalTrack` — a track discovered through an external provider, tied to an
  `ExternalLibrary` and optionally to a Songhive `Track`.
- `ExternalSyncRun` — a record of each sync attempt, including status,
  triggered-by, and error details.

API surfaces:

- `api/routes/external_libraries.py` — user-facing CRUD, sync, sync-run listing,
  and track management under `/api/v1/external-libraries/...`.
- `api/routes/admin_external_libraries.py` — admin CRUD, sync, and track
  management under `/api/v1/admin/external-libraries/...`.

User-created external libraries are governed by
`config.external_libraries.allow_user_created_libraries` and the
allow/deny provider lists. Admin libraries can be included in the public library
index when `allow_admin_library_index_inclusion` is enabled.

The built-in `local` provider (`external/_local.py`) lets admins mount
server-side directories as external libraries. Root paths are restricted to an
allowlist configured in `external_libraries.local_roots` for safety. Files under
the root are enumerated recursively, metadata is read from embedded tags, and
the directory is treated as a local audio source for streaming and download.
A separate filesystem watchdog process (`external/watchdog.py`) monitors enabled
local libraries and enqueues incremental or scoped syncs through
`sync_external_library_task` as files change, with a quiet window to coalesce
bursts of events. The watchdog is started via `songhive watch-external-libraries`
and is also available as a `watcher` container in `docker-compose.yml`. If no
local libraries are configured, the process polls the database every few seconds
and starts watching automatically once libraries are created, so it does not need
to be restarted after the stack is already running.

The watcher is intentionally a standalone process rather than a thread inside the
main web server: filesystem watches can be long-lived, are best as a single
instance per host, and have a different failure/restart profile than HTTP
workers. In a non-Docker deployment you should run it as a separate process
(`songhive watch-external-libraries`) or under a supervisor such as systemd.

#### Local provider settings

A local external library stores the following adapter config in `ExternalLibrary.config`:

| Key                  | Required | Default      | Description                                                                 |
|----------------------|----------|--------------|-----------------------------------------------------------------------------|
| `root`               | yes      | —            | Absolute or relative path to the directory to scan.                         |
| `extensions`         | no       | all audio    | List of file extensions to index, e.g. `[".mp3", ".flac"]`.               |
| `recursive`          | no       | `true`       | Whether to scan subdirectories.                                             |
| `follow_symlinks`    | no       | `false`      | If `false`, symlinks are ignored; if `true`, resolved targets must stay within `root`. |
| `exclude`            | no       | `[]`         | `fnmatch` patterns applied to root-relative paths.                          |
| `allow_hashing`      | no       | `true`       | Whether to compute a SHA-256 hash for new/updated files.                    |
| `fast_hash`          | no       | `false`      | If `true`, hash the raw file bytes; otherwise use ffmpeg audio-only hashing. |
| `allow_write_tags`   | no       | `false`      | Whether Songhive metadata edits may be written back to the source files.    |
| `allow_rename_source`| no       | `false`      | Whether the `rename_source` operation may rename the backing file.          |
| `allow_delete_source`| no       | `false`      | Whether the `delete_source` operation may remove the backing file.          |

The global `external_libraries.local_roots` allowlist is required: a library's
`root` must resolve to a path inside one of the configured roots, or validation
fails.

The web UI renders a per-provider configuration form instead of raw JSON.
Provider form templates live in
`frontend/src/config/externalLibraryProviderTemplates.ts`. Each template entry
lists the JSON property name, i18n label/description keys, field type (string,
number, boolean, enum, or comma-separated string array), and default value.
`ExternalLibraryEditView` switches the displayed fields whenever the provider
`<select>` changes, and falls back to a plain JSON textarea for providers that do
not have a template yet.

Sync and tasks:

- `tasks/external_libraries.py` defines three Celery tasks:
  - `sync_external_library_task` runs a manual or scheduled sync, enumerating
    provider items through `external/sync.py` and upserting `ExternalTrack` rows.
  - `scan_scheduled_syncs_task` scans for due external libraries and enqueues
    sync tasks while skipping libraries with an active run.
  - `write_back_metadata_task` applies local metadata edits to the provider when
    the adapter supports `write_tags`.
  - `rename_source` lets track owners and admins rename the backing source file
    from the track edit page when the adapter supports it.
  - Destructive source deletion requires both `write_tags` and `delete_source`
    capabilities, the global `allow_destructive_delete` setting, and an explicit
    `confirm: "DELETE"` from the caller. Tracks may optionally be removed from
    Songhive at the same time.

Streaming and downloads:

- `services/streaming.resolve_external_stream` loads an external track and opens
  a provider stream (`path`, `iterator`, or `url`).
- `streaming/handler.py` falls back from local `StoredFile` playback to external
  streams, preserving auth, ACL, history, and now-playing broadcasts.
- `api/routes/tracks.py` provides `GET /api/v1/tracks/{track_id}/download`,
  proxying external bytes through Songhive and honoring `Content-Disposition`
  and `disposition=inline|attachment`.

Secrets and audit:

- `services/secrets.py` encrypts/decrypts provider configs at rest and redacts
  them in API responses and audit logs.
- All mutating external-library and external-track endpoints log an `AuditLog`
  row with a sanitized `details` dictionary.

### Duplicate detection

When an uploaded audio file's audio-only SHA-256 hash matches an `ExternalTrack`
in `active` or `shadowed` state, `services/import_.py` raises an
`ExternalDuplicateError`. The `POST /api/v1/files/upload` and
`POST /api/v1/files/upload/bulk` endpoints return a 409 response with an
`ExternalDuplicateWarning` containing a short-lived Redis token.
The caller can then resolve the conflict via
`POST /api/v1/files/upload/resolve-duplicate` with `action=keep_local` (create a
local Songhive track and keep the uploaded file) or `action=discard_upload`
(reuse the existing external track and remove the uploaded stored file).

---

## Database Migrations

Schema changes are managed with [Alembic](https://alembic.sqlalchemy.org/).  The
``songhive/migrations/`` package contains the Alembic environment, the
``versions/`` directory, and an empty ``base`` revision that marks the pre-
migration schema baseline.  New installs receive the current schema from
``Base.metadata.create_all`` and are then stamped at ``head``; existing
production databases are stamped at ``base`` and upgraded normally.

Migrations run automatically when ``create_app`` is called, and the Docker
entrypoint runs ``songhive admin migrate`` before starting the web server or
Celery workers.  ``ensure_migrated`` acquires a backend-specific lock for the
whole operation: a PostgreSQL advisory lock for Postgres, or a ``fcntl`` file
lock on a companion file for SQLite.  This prevents the web server and worker
from racing to create the baseline schema on a fresh Docker Compose install.
Admins can also trigger them manually:

```bash
python -m songhive admin migrate
```

To autogenerate a revision after a model change (from the repository root,
with ``SONGHIVE_DATABASE__URL`` set or ``database.url`` configured):

```bash
alembic revision --autogenerate -m "add example column"
```

---

## Authentication & Authorization

### Authentication flow

1. **Login** — username/password → bcrypt verify → issue `TokenPair`
   (short-lived JWT access token + long-lived opaque refresh token stored in
   Redis). Each refresh token also records IP, user agent, creation time and
   expiry, forming a user session.
2. **Refresh** — opaque refresh token → Redis lookup → rotate (revoke old,
   issue new pair).
3. **Revoke** — single token or all tokens for a user (Redis key deletion).
   Access tokens now carry a `jti` claim, and revocation also adds that JTI to
   a Redis deny-list. The JWT middleware checks the deny-list so revoked
   sessions cannot continue using their existing access token until expiry.
4. **Session management** — users can list their active refresh-token sessions
   (including the current one) and revoke any session individually, revoking the
   current session ends the user's login.
5. **OAuth2** — authlib `authorization_code` flow for third-party app access.
6. **Invite-only registration** — controlled by `RegistrationMode` config
   setting; `Invite` codes with optional `max_uses` and `expires_at`.

### Authorization

- **JWT middleware** — `api/middleware/auth.py` decodes the bearer token and
  injects the current `User` via FastAPI's dependency system.
- **Role-based** — `UserRole.ADMIN` / `MODERATOR` / `USER`; `require_admin`
  dependency enforces admin-only routes.
- **ACL service** (`services/acl.py`) — three-level visibility check augmented
  with `ShareGrant` (owner grants a named user) and `ShareToken` (revocable
  short-link cookie).
- **Rate limiting** — Redis sliding-window; `rate_limit` (IP), `rate_limit_user_or_ip`
  (authenticated users keyed by id), and `rate_limit_account` (always per-user)
  FastAPI dependencies. Media `DELETE` endpoints use `rate_limit_account` for
  per-user rate limiting on destructive operations. Fails open when Redis is unavailable.

---

## File Storage & Upload Pipeline

1. Client lists visible files via `GET /api/v1/files/`, uploads via
   `POST /api/v1/files/upload`, or bulk-uploads via
   `POST /api/v1/files/upload/bulk`. The bulk endpoint uses the same per-IP
   rate limit as the single-file upload and enforces per-request limits on the
   number of files and total request size.
2. `StorageService` (facade over `StorageBackend`) validates size limit,
   computes SHA-256, deduplicates by hash, writes to backend.
3. A `StoredFile` row is created (content-addressable, owner/visibility set).
4. An `Upload` row is created linking the stored file to a track (or pending
   import).
5. A Celery `import_` task processes metadata, creates/updates `Track`,
   `Album`, `Artist` records, and optionally triggers `enrich_track`.
6. `enrich_track` enqueues `sync_track_tags` and `enrich_images` on success;
   the latter fetches artist images and any missing album covers.
7. A separate Celery `transcoding` task pre-transcodes to requested formats
   and stores results as `TranscodedFile` rows pointing back to a `StoredFile`.

**Orphan GC** — the `storage.cleanup_orphaned_files` Celery task runs on the
configured crontab (default: daily at 03:00) and deletes `StoredFile` rows
(and their backing files) not referenced by any `Track`, `Album`, or `Upload`.

**Cascade Deletion** — `services/deletion.py` provides centralized deletion
logic for `Track`, `StoredFile`, `Album`, `Artist`, `Playlist`, and `Library`.
`DELETE` endpoints on the corresponding routes accept a `recursive` query
parameter; albums default to recursive deletion while other collections default
to non-recursive. A dedicated `DELETE /api/v1/tracks/bulk` endpoint accepts a
list of track IDs and delegates to `delete_tracks_bulk` in the deletion service,
applying the same ACL and rate-limiting checks as single-track deletion.
Deleting a track removes its `Upload`, `LibraryTrack`,
`PlaylistTrack`, `Favorite`, `ListeningHistory`, `TranscodedFile`, `ShareGrant`,
`ShareToken`, and `Report` rows and deletes the underlying `StoredFile` once it
is unreferenced. Deleting a stored file removes all tracks that use it as their
audio source and clears `cover_file_id`/`image_file_id` references on
albums/artists before removing the backing object. Deleting an external library
cascades through its linked `ExternalTrack` rows, removing each Songhive track
and its dependents (playlist entries, favorites, etc.), cleaning up empty
albums/artists, and removing the underlying `Library` only when it is empty.
Recursive deletion collects unpublish information for public tracks and enqueues
`Delete(Tombstone)` ActivityPub activities.

---

## Streaming & Transcoding

- **Tornado `StreamHandler`** (`streaming/handler.py`) handles
  `GET /api/v1/stream/{track_id}`, resolves the best `StoredFile` via
  `services/streaming.py`, and streams with native range-request support.
- **Transcode cache** — on first request for a (format, bitrate) combination,
  the `Transcoder` writes the output to a temp file, which is then stored as
  a `StoredFile` and indexed in `TranscodedFile`. Subsequent requests serve
  the cached file directly (skipping ffmpeg) when `transcode_cache_enabled`.
- **Bitrate enforcement** — `effective_bitrate()` in `config/schema.py`
  computes the minimum of: requested bitrate, instance max, and per-role max
  (`max_bitrate_by_role` keyed by `User.role`).
- **Supported formats**: MP3, OGG (Vorbis), FLAC, AAC (M4A), Opus.

---

## Federation

Federation is powered by [pubby](https://github.com/blacklight/pubby) mounted
on the FastAPI app via its FastAPI adapter. Per-user actor routes and WebFinger
discovery are in `api/routes/federation.py`. The general-purpose primitives are
delegated to pubby:

- Outbound plaintext→HTML rendering (escaping, URL linkification,
  `rel="tag"`/`rel="me"` anchors, `Hashtag` and `PropertyValue`
  tag/attachment builders) and `Audio` object content/duration formatting
  (`set_object_content`, `format_duration`) come from `pubby.content`; the
  instance's `/hashtags/{name}` route convention is injected via
  `federation/_common.py`'s `get_hashtag_url`.
- Instance allow/block matching (`normalize_domain`, `extract_domain`,
  `is_domain_blocked`) comes from `pubby.moderation`; `services/federation.py`
  keeps thin wrappers that inject `config.federation.allowed_instances` /
  `blocked_instances`.
- Async→sync database URL conversion for pubby's synchronous SQLAlchemy
  storage is `pubby.storage.adapters.db.to_sync_url` (applied by
  `init_db_storage` inside `create_activitypub_storage`).
- Actor private-key provisioning is `pubby.crypto.ensure_private_key_file`.
- Follower inbox collection is `pubby.collect_inboxes`; one-shot signed
  delivery is `pubby.deliver_activity` (the Celery task keeps the retry
  policy).

Songhive keeps orchestration: Celery tasks and their retry policy, per-user
actor documents built from the `User` model, `federation_*` table naming, and
the HTTP routes.

**Actor model:**

- Each `User` has `actor_url`, `private_key_pem`, and `public_key_pem` columns.
- New users are provisioned with RSA keypairs on creation when federation is
  enabled. Existing users can be back-filled via
  `songhive admin provision-federation-keys`.
- Each user is reachable at:
  - `https://{instance_domain}/users/{username}` — canonical `Person` actor
  - `https://{instance_domain}/@{username}` — Mastodon-style alias
  - `https://{instance_domain}/.well-known/webfinger?resource=acct:{username}@{instance_domain}`

**Activity lifecycle:**

- Uploaded tracks are published as `Audio` objects via `Create` activity.
  This happens on every track-creation path: the `/api/v1/files/upload`
  endpoints (single, bulk, and external-duplicate resolution), the
  `/{library_id}/tracks` upload endpoints, and the background `process_upload`
  Celery task.
- The published `Audio` object carries the track's `description` (a free-text
  field settable at upload time or via `PATCH /tracks/{id}`) as its
  `content`: the text is HTML-escaped, http(s) URLs become anchors with
  scheme-less link text, and `#hashtags` become `rel="tag"` links to this
  instance's `/hashtags/{name}` pages. Hashtags found in the description are
  also appended to the object's `tag` list, and the media download URL is
  attached as a `Document` with the audio MIME type.
- Each public lifecycle gets a fresh `Track.federation_object_id` (generated
  on every transition to public) so a previous `Tombstone` at the same URL
  cannot block re-publication.
- `POST /api/v1/tracks/{id}/publish` lets the track's owner re-publish a
  public track to the fediverse at any time (the "Fediverse" tab of the
  share dialog). It accepts an optional `status` — a one-off post text used
  as the `Create(Audio)` object's `content` instead of the stored
  `description`; the status is never persisted on the track. Each manual
  publication mints a fresh `federation_object_id` so every post is a
  distinct remote object.
- `Delete(Tombstone)` is sent when a track is made non-public or deleted.
- Profile changes (display name, bio, avatar, links) refresh the cached actor
  document via `sync_user_actor` and are pushed to follower inboxes as
  `Update(Person)` activities.
- Profile links are emitted as `PropertyValue` attachments whose `value` is an
  HTML anchor (`<a href="..." rel="me">`) so remote servers render them as
  clickable fields. The URL is only linkified when it is a well-formed
  http(s) URL with a host and no HTML-breaking characters; malformed values
  are emitted as escaped plain text instead. Anchor text omits the `http(s)://`
  scheme for readability.
- The actor `summary` is rendered from the bio as escaped HTML with http(s)
  URLs linkified into anchors (scheme-less link text); sentence punctuation
  wrapping a URL stays outside the anchor, and URL-looking text that fails
  validation is left as escaped text.
- Following/unfollowing uses standard AP `Follow`/`Undo(Follow)` activities.
- `track.genre` is split into multiple `Hashtag` tags on the published
  `Audio` object, with spaces converted to underscores. Hashtag tags include
  an `href` pointing at the instance's `/hashtags/{name}` page.
- Per-actor follower isolation is delegated to pubby's `target_actor_id`.

**Instance-level actor:**

- `_setup_federation()` in `api/app.py` configures an `Application`-type
  actor via pubby's `ActorConfig` and mounts both ActivityPub and Mastodon API
  compatibility endpoints.

**Domain allow/block lists** (`federation.allowed_instances` /
`federation.blocked_instances`) are enforced at several seams:

- `POST /users/{username}/inbox` in `api/routes/federation.py` rejects with
  403 before queueing (`is_domain_allowed`).
- `tasks/federation.py`'s `process_incoming` passes the lists to pubby's
  `InboxProcessor`, which drops the activity before signature verification.
- The instance-level `/ap/inbox` and pubby's outbound fan-out get the same
  filtering via `allowed_instances`/`blocked_instances` on
  `ActivityPubHandler` (`_setup_federation` in `api/app.py`).
- `tasks/federation.py`'s `deliver_activity` drops outbound deliveries to
  blocked domains before signing.

---

## Content Moderation

- `Report` model stores user-submitted content flags (target type/id, reason,
  description, status, reviewer).
- Public submission: `POST /api/v1/reports`.
- Admin review: `GET/PATCH /api/v1/admin/reports`.
- `AuditLog` records administrative and security-relevant actions (actor, target
  type/id, IP address, JSON details).

---

## Sharing

Two independent mechanisms for sharing private content:

| Mechanism     | Model         | Route prefix         | Description                             |
|---------------|---------------|----------------------|-----------------------------------------|
| Share grant   | `ShareGrant`  | `/api/v1/shares`     | Owner grants a named user access        |
| Share URL     | `ShareToken`  | `/api/v1/share-urls` | Revocable short link; token hash stored |
| URL resolver  | —             | `/api/v1/share/{token}` | Resolves raw token → HTML, JSON, or direct audio download |

The URL resolver is content-negotiated:

- Browsers and crawlers receive a rendered HTML preview page with OpenGraph
  metadata, an audio player for tracks, and track listings for albums,
  playlists, libraries, and artists.
- API clients that send `Accept: application/json` (including the web UI's
  share preview) still receive a `302` redirect to the item's public JSON
  endpoint, with a short-lived `share_token` cookie.
- Audio file shares redirect directly to the file download URL, and
  `?download=true` forces a direct audio download for tracks or files.

The ACL service (`services/acl.py`) checks grants and tokens transparently
via the `require_access` FastAPI dependency used by resource routes.

---

## Task Queue (Celery)

All background work is handled by Celery workers. Redis is the broker
(db 1) and result backend (db 2).

| Task module          | Responsibilities                                          |
|----------------------|-----------------------------------------------------------|
| `tasks/import_.py`   | File processing, tag extraction, track/album/artist upsert|
| `tasks/federation.py`| Activity delivery, inbox processing, key provisioning       |
| `tasks/transcoding.py`| Pre-transcode to common formats, cache result            |
| `tasks/email.py`     | Verification emails, password-reset emails                |
| `tasks/musicbrainz.py`| MusicBrainz + Cover Art Archive metadata enrichment      |
| `tasks/images.py`    | Artist image + Cover Art Archive cover enrichment         |
| `tasks/storage.py`   | Orphaned `StoredFile` GC, audio-only hash rehash (scheduled) |

The `cleanup_orphaned_files_schedule` config accepts any 5-field cron
expression.

Each task's async work is executed with ``asyncio.run(...)``. Because
``asyncpg`` connections are bound to the event loop that created them, every
task disposes the shared async engine and resets the global session factory
before the loop closes, ensuring the next task gets a fresh pool.

---

## Email

SMTP-based email is configured via the `email` config section. Celery tasks
in `tasks/email.py` enqueue verification and password-reset messages
asynchronously. `EmailNotConfiguredError` is raised when the SMTP host or
`from_address` is missing.

---

## Track Metadata and Audio-Only Content Hashing

Uploaded audio files are deduplicated and addressed by an **audio-only SHA-256
hash**. The hash is computed with `ffmpeg -map 0:a -c copy -f streamhash` over
the raw audio bitstream, ignoring container metadata, embedded tags, and cover
art. This means:

- Two files containing the same recording but with different tags have the same
  hash and share a single `StoredFile` row and storage path.
- Tags and cover art can be rewritten in place without changing `sha256` or
  `storage_path`; only `StoredFile.size` is updated.

The tag rewrite is performed by the `sync_track_tags` Celery task
(`songhive/tasks/tags.py`). The task:

1. Acquires a Redis lock at `sync_tags:{track_id}` (`nx=True`, `ex=300`).
2. Loads the track with its artist, album, audio file, track image, and album
   cover relations.
3. Resolves cover art in this order:
   1. Track `image_file_id`
   2. Album `cover_file_id`
   3. No cover
4. Retrieves the audio file locally and writes the current DB metadata into the
   embedded tags using `mutagen`.
5. Reconciles the track's `Genre` associations from `track.genre` or from the
   parent album when the track has no explicit genre, creates the corresponding
   hashtag associations via `genres_to_hashtags`, and propagates the album's
   genre from the intersection of its tracks' explicit genres.
6. Updates `StoredFile.size`. For S3, re-uploads the rewritten file to the same
   key; for local storage, the file is already in place.
7. Releases the lock.

Tag sync is triggered automatically by metadata-mutating API operations
(track/album/artist `PATCH`, track/album cover upload and delete) and by
successful MusicBrainz enrichment. Manual bulk triggers are provided by
`POST /api/v1/admin/sync-tags` and `songhive admin sync-tags` (with optional
`--track-id`, `--album-id`, `--artist-id`, `--library-id`, or `--all`); the
admin web UI exposes the same options under `/admin/tasks`.

Image enrichment (artist images + album covers) is normally triggered
automatically by MusicBrainz metadata enrichment. It can also be run manually
in bulk via `POST /api/v1/admin/enrich-images` and
`songhive admin enrich-images` (with `--artist-id`, `--album-id`, or `--all`,
plus `--force` to re-process already-enriched entities and `--dry-run` to
preview counts).

Artist images are resolved in this order: (1) MusicBrainz image URL
relationships, (2) Wikidata `P18` image claims via the artist's Wikidata
relationship, (3) archived/known image hosts such as `web.archive.org`
(Spotify CDN) and `i.scdn.co`. If no image can be downloaded, the artist is
not marked as enriched, so future syncs will continue to retry.

To migrate an existing library that was stored before audio-only hashing, run
`songhive admin rehash-audio` (with `--dry-run` to preview) or use
`POST /api/v1/admin/rehash-audio` from the admin UI. The task re-hashes audio
files, moves/renames the backing files to the new hash-based paths, and merges
duplicate `StoredFile` rows.

Federation keys and actor URLs can be back-filled with
`songhive admin provision-federation-keys` or `POST /api/v1/admin/provision-federation-keys`.

Admins can also inspect and control the Celery worker pool from the UI. The
`GET /api/v1/admin/celery/tasks` endpoint lists all tasks currently running on
workers (including per-task runtime, worker, args and kwargs), while
`POST /api/v1/admin/celery/terminate` accepts a list of task ids and revokes them
with `terminate=True` so they are killed on the worker(s) that are running them.
The admin UI exposes these under `/admin/celery` with bulk selection.

---

## Storage Backends

| Backend | Class          | Description                                     |
|---------|----------------|-------------------------------------------------|
| Local   | `LocalStorage` | Files stored under `storage.local_path`         |
| S3      | `S3Storage`    | S3-compatible object storage; optional CDN prefix|

Backend is selected via `storage.backend` config. Both implement the abstract
`StorageBackend` interface (`storage/base.py`). `FileSizeLimitExceededError`
is raised when an upload exceeds `storage.max_upload_size`.

---

## WebSockets

`ws/events.py` implements a Tornado `WebSocketHandler` that:

- Validates the `Origin` header against `config.server.cors_origins`.
- Authenticates the connecting user via a JWT access token (passed as a query
  parameter or cookie on the initial handshake).
- Broadcasts real-time events (import progress, federation notifications, etc.)
  to authenticated clients.

---

## Frontend

Vue.js 3 + TypeScript SPA, bundled with Vite.

| File/Dir | Role |
|----------|------|
| `frontend/src/main.ts` | App bootstrap, Pinia + i18n + router mount, theme apply |
| `frontend/src/App.vue` | Root component (`<RouterView />`) |
| `frontend/src/router/` | Vue Router (history mode) with global auth/admin guard |
| `frontend/src/stores/` | Pinia stores (auth, theme, toast, confirm, player) |
| `frontend/src/components/ui/` | Headless base components (button, input, select, avatar, table, pagination, search, context menu, entity actions) |
| `frontend/src/components/feedback/` | Toast, banner, spinner, skeleton, modal, confirm dialog |
| `frontend/src/components/entity/` | Reusable entity grid/list components (e.g. `BulkEditableGrid` for bulk selection and deletion) |
| `frontend/src/components/admin/` | Admin-specific shared components (e.g. `StatCard` for the dashboard) |
| `frontend/src/components/player/` | Player bar slot (Phase 3 placeholder) |
| `frontend/src/layouts/` | App, auth, and admin layouts |
| `frontend/src/views/` | Page-level components, including `views/admin/` (Dashboard, Users, Settings, Reports, Invites, Audit, Tasks, Celery) behind the `/admin` guard (Home, Library, Album/Artist/Track/Playlist lists and details, History, Favorites, Files, File detail, Radio station list/create/play, About, Login, Register, PasswordReset, VerifyEmail, Profile, plus 403/404 and placeholder views) |
| `frontend/src/api/` | Typed HTTP client (`openapi-typescript` generated `types.ts`), per-resource modules including `admin.ts` for the admin panel, WebSocket event bus, stream URL helper |
| `frontend/src/i18n/` | `vue-i18n` setup with lazy-loaded locales |
| `frontend/src/styles/tokens.css` | CSS custom properties for theming |

The frontend is also a Progressive Web App. `/manifest.webmanifest` is served
from the backend so the PWA name follows the configured instance name and the
manifest `theme_color`/`background_color` react to the user's selected
light/dark theme. `frontend/public/pwa/` contains generated icon variants
(including `maskable-*` icons for Android adaptive icons), and
`frontend/public/sw.js` provides a lightweight offline shell cache.

Build output is served as static files by the backend (or a CDN). The FastAPI
app sets `router.default` to an ASGI handler that serves files directly from
`songhive/static/` and falls back to `index.html` for unhandled non-API paths,
so the Vue Router handles deep links such as `/verify-email?token=...`.

---

## API Design

REST API under `/api/v1/`:

```
/api/v1/
├── auth/           # Login, register, token refresh, password reset, verify email, sessions
├── users/          # User profiles, avatar, links, invite management
├── artists/        # Artist CRUD + search
├── albums/         # Album CRUD + search
├── tracks/         # Track CRUD + search
├── files/          # Generic file upload/list/download (StoredFile)
├── libraries/      # Library management + add/remove tracks/albums/artists
├── playlists/      # Playlist CRUD + add/remove/reorder tracks/albums/artists + list tracks
├── favorites/      # Favorites/bookmarks
├── history/        # Listening history
├── radios/         # Dynamic radio generation
├── instance/       # Public instance metadata (Mastodon-compatible)
├── shares/         # Share grants (owner → specific user)
├── share-urls/     # Share URL tokens (revocable short links)
├── share/{token}   # Public short-URL resolver
├── reports/        # Content moderation reports (submit)
├── admin/          # Admin: settings, stats, user management, report review
└── stream/{id}     # Audio streaming (Tornado handler, bypasses FastAPI)
```

**Federation endpoints** (mounted by pubby when federation is enabled):

```
/users/{username}               # Per-user ActivityPub actor document
/@{username}                    # Mastodon-style alias / browser redirect
/.well-known/webfinger          # WebFinger discovery
/.well-known/nodeinfo           # NodeInfo (Mastodon compat)
/ap/actor                       # Instance-level Application actor
/ap/inbox                       # Instance inbox
/api/v1/*/                      # Mastodon-compatible API (pubby adapter), except /api/v1/instance which is provided by Songhive and always available
```

---

## Deployment

Docker Compose (`docker-compose.yml`) provides a reference deployment:

- `songhive` — application container (Tornado server)
- `celery` — Celery worker container (same image, different entrypoint)
- `postgres` — PostgreSQL database
- `redis` — Redis (broker + cache + sessions)
- `nginx` — Reverse proxy (`docker/nginx.conf`). It proxies federation routes
(`/.well-known/*`, `/ap/*`, `/users/<user>`, `/@<user>`, etc.) to the
application and performs content negotiation for `/@<user>` and
`/users/<user>`: requests that accept `application/activity+json` or
`application/ld+json` are proxied to the backend, while browser `text/html`
requests fall through to the Vue SPA.

Persistent data is stored under `volumes/`.

### systemd

For non-Docker deployments, `config/systemd/` contains unit files for running
Songhive as a systemd service. The `install.sh` script automates the setup:

- Creates a Python virtual environment
- Installs Songhive from the local checkout
- Copies `config.toml.example` to the system (`/etc/songhive`) or user
  (`~/.config/songhive`) config directory
- Creates data, cache, and log directories
- Installs the systemd units and sets the correct `ExecStart` paths

The master `songhive.service` unit starts three dependent units:

- `songhive-server.service` — main web server (`songhive`)
- `songhive-celery.service` — Celery worker and scheduler
- `songhive-watch-extlib.service` — external-library watchdog
