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
│   │   ├── users.py        # User profiles, avatar, links, password change
│   │   ├── artists.py
│   │   ├── albums.py
│   │   ├── tracks.py
│   │   ├── playlists.py
│   │   ├── libraries.py
│   │   ├── favorites.py
│   │   ├── history.py      # Listening history
│   │   ├── radios.py       # Dynamic radio generation
│   │   ├── files.py        # Generic file upload/download (StoredFile)
│   │   ├── shares.py       # Share grants (owner→specific user)
│   │   ├── share_urls.py   # Share URL tokens (revocable short links)
│   │   ├── share.py        # Public token resolver (redirects + sets cookie)
│   │   ├── reports.py      # Content moderation reports + admin review
│   │   ├── federation.py   # Per-user ActivityPub actors + WebFinger
│   │   └── admin.py        # Admin endpoints (settings, stats, user management)
│   └── middleware/
│       ├── auth.py         # JWT decode middleware + access-token helpers
│       └── rate_limit.py   # Redis sliding-window rate limiting (IP / user)
├── migrations/             # Alembic database migrations
│   ├── env.py              # Migration environment (imports all models)
│   ├── script.py.mako      # Template for generated revisions
│   └── versions/           # Revision scripts
├── models/                 # SQLAlchemy mapped models + shared enums
│   ├── base.py             # DeclarativeBase, UUID PK, timestamps, async session factory
│   ├── _enums.py           # Visibility enum (private / local / public)
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
│   └── setting.py          # Runtime-editable instance settings (key/JSON-value)
├── services/               # Business logic layer
│   ├── acl.py              # Three-level visibility + share-grant + share-token ACL
│   ├── auth.py             # User lookup, password hashing, session helpers
│   ├── audit.py            # Audit log helpers
│   ├── email.py            # SMTP email (verification, password reset)
│   ├── federation.py       # Actor provisioning, domain allow/block, inbox dispatch
│   ├── import_.py          # Import pipeline orchestration
│   ├── metadata.py         # Tag extraction coordination
│   ├── music.py            # Music library helpers
│   ├── musicbrainz.py      # MusicBrainz + Cover Art Archive enrichment (async httpx)
│   ├── redis.py            # Redis client lifecycle
│   ├── reports.py          # Content report CRUD
│   ├── settings.py         # Instance settings with Redis cache + config overlay
│   ├── sharing.py          # Share-grant and share-token CRUD
│   ├── stats.py            # Admin dashboard statistics
│   ├── storage.py          # StorageService facade (delegates to storage backend)
│   └── streaming.py        # Track file resolution, transcode cache, history recording
├── federation/             # ActivityPub per-user federation
│   ├── _common.py          # URL builders
│   ├── actors.py           # Actor document generation, federation storage helpers
│   ├── activities.py       # Activity creation (Create, Delete, Follow, etc.)
│   ├── serializers.py      # Track → ActivityPub Audio object mapping
│   └── storage.py          # pubby storage adapter (SQLAlchemy-backed)
├── users/                  # User management
│   ├── manager.py          # User CRUD, password management
│   ├── invites.py          # Invite-code creation, validation, consumption
│   ├── oauth.py            # OAuth2 provider setup (authlib)
│   └── tokens.py           # JWT access + opaque refresh token issuance/rotation/revocation
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
│   ├── musicbrainz.py      # MusicBrainz enrichment tasks
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
3. `config.toml` (searched at `./config.toml` then `~/.config/songhive/config.toml`)
4. Field defaults

The root schema is `SonghiveConfig` (a Pydantic `BaseSettings`), composed of
these subsections:

| Section        | Key settings                                                  |
|----------------|---------------------------------------------------------------|
| `server`       | host, port, debug, cors_origins, trusted_proxy_hops           |
| `database`     | url (asyncpg), pool_size, max_overflow                        |
| `redis`        | url                                                           |
| `celery`       | broker_url, result_backend, cleanup_orphaned_files_schedule   |
| `storage`      | backend (local/s3), local_path, s3_*, cdn_prefix, max_upload_size |
| `federation`   | enabled, instance_domain, instance_name, private_key_path, allow/block lists |
| `auth`         | secret_key, token TTLs, rate_limit_enabled, rate_limit_window_seconds, trusted_proxy_hops |
| `email`        | smtp_host, smtp_port, smtp_user, from_address, tls settings   |
| `musicbrainz`  | enabled, user_agent, cover_art settings                       |
| `imports`      | auto_enrich, import_visibility                                |
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

**Visibility levels** (`Visibility` enum, applies to tracks, albums, artists,
libraries, stored files):

- `private` — visible only to the owner (and users with a `ShareGrant`)
- `local` — visible to authenticated users on the same instance
- `public` — visible to everyone including federated instances

### Genres

Genres are stored in a dedicated `Genre` table and linked to `Track` and
`Album` through `GenreTrack` and `GenreAlbum` association tables. The free-text
`Track.genre` and `Album.genre` columns remain the source of truth for embedded
metadata round-trips, while the normalised tables enable browsing, counting, and
filtering. Genres will also feed the hashtag system: the genre string will be
split and mapped to valid hashtag names so genre-derived hashtags appear
alongside user-created ones.

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
Celery workers.  Admins can also trigger them manually:

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
   Redis).
2. **Refresh** — opaque refresh token → Redis lookup → rotate (revoke old,
   issue new pair).
3. **Revoke** — single token or all tokens for a user (Redis key deletion).
4. **OAuth2** — authlib `authorization_code` flow for third-party app access.
5. **Invite-only registration** — controlled by `RegistrationMode` config
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

1. Client lists visible files via `GET /api/v1/files/` or uploads via
   `POST /api/v1/files/upload`.
2. `StorageService` (facade over `StorageBackend`) validates size limit,
   computes SHA-256, deduplicates by hash, writes to backend.
3. A `StoredFile` row is created (content-addressable, owner/visibility set).
4. An `Upload` row is created linking the stored file to a track (or pending
   import).
5. A Celery `import_` task processes metadata, creates/updates `Track`,
   `Album`, `Artist` records, and optionally triggers `enrich_track`.
6. A separate Celery `transcoding` task pre-transcodes to requested formats
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
albums/artists before removing the backing object. Recursive deletion collects
unpublish information for public tracks and enqueues `Delete(Tombstone)`
ActivityPub activities.

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
discovery are in `api/routes/federation.py`.

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
- Each public lifecycle gets a fresh `Track.federation_object_id` (generated
  on every transition to public) so a previous `Tombstone` at the same URL
  cannot block re-publication.
- `Delete(Tombstone)` is sent when a track is made non-public or deleted.
- Following/unfollowing uses standard AP `Follow`/`Undo(Follow)` activities.
- Per-actor follower isolation is delegated to pubby's `target_actor_id`.

**Instance-level actor:**

- `_setup_federation()` in `api/app.py` configures an `Application`-type
  actor via pubby's `ActorConfig` and mounts both ActivityPub and Mastodon API
  compatibility endpoints.

**Domain allow/block lists** are checked in `services/federation.py`
(`is_domain_allowed`) before processing incoming activities.

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
| `tasks/federation.py`| Activity delivery, inbox processing                       |
| `tasks/transcoding.py`| Pre-transcode to common formats, cache result            |
| `tasks/email.py`     | Verification emails, password-reset emails                |
| `tasks/musicbrainz.py`| MusicBrainz + Cover Art Archive metadata enrichment      |
| `tasks/storage.py`   | Orphaned `StoredFile` GC (scheduled, default 03:00 daily) |

The `cleanup_orphaned_files_schedule` config accepts any 5-field cron
expression.

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
4. Retrieves the audio file locally, writes the current DB metadata into the
   embedded tags using `mutagen`, and updates `StoredFile.size`.
5. For S3, re-uploads the rewritten file to the same key; for local storage,
   the file is already in place.
6. Releases the lock.

Tag sync is triggered automatically by metadata-mutating API operations
(track/album/artist `PATCH`, track/album cover upload and delete) and by
successful MusicBrainz enrichment. Manual bulk triggers are provided by
`POST /api/v1/admin/sync-tags` and `songhive admin sync-tags` (with optional
`--track-id`, `--album-id`, `--artist-id`, `--library-id`, or `--all`).

To migrate an existing library that was stored before audio-only hashing, run
`songhive admin rehash-audio` (with `--dry-run` to preview). The command
re-hashes audio files, moves/renames the backing files to the new hash-based
paths, and merges duplicate `StoredFile` rows.

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
| `frontend/src/views/` | Page-level components, including `views/admin/` (Dashboard, Users, Settings, Reports, Invites, Audit, Storage) behind the `/admin` guard (Home, Library, Album/Artist/Track/Playlist lists and details, History, Favorites, Files, File detail, Radio station list/create/play, About, Login, Register, PasswordReset, VerifyEmail, Profile, plus 403/404 and placeholder views) |
| `frontend/src/api/` | Typed HTTP client (`openapi-typescript` generated `types.ts`), per-resource modules including `admin.ts` for the admin panel, WebSocket event bus, stream URL helper |
| `frontend/src/i18n/` | `vue-i18n` setup with lazy-loaded locales |
| `frontend/src/styles/tokens.css` | CSS custom properties for theming |

Build output is served as static files by the backend (or a CDN). The FastAPI
app sets `router.default` to an ASGI handler that serves files directly from
`songhive/static/` and falls back to `index.html` for unhandled non-API paths,
so the Vue Router handles deep links such as `/verify-email?token=...`.

---

## API Design

REST API under `/api/v1/`:

```
/api/v1/
├── auth/           # Login, register, token refresh, password reset, verify email
├── users/          # User profiles, avatar, links, invite management
├── artists/        # Artist CRUD + search
├── albums/         # Album CRUD + search
├── tracks/         # Track CRUD + search
├── files/          # Generic file upload/list/download (StoredFile)
├── libraries/      # Library management + add/remove tracks/albums/artists
├── playlists/      # Playlist CRUD + add/remove tracks/albums/artists + list tracks
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
