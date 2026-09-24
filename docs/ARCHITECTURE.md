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
│   ├── cookies.py          # Set/clear HttpOnly auth cookies + double-submit CSRF cookie
│   ├── deps.py             # Dependency injection (DB session, current user, config, Redis, storage)
│   ├── errors.py           # RFC 7807 problem-detail exception handlers
│   ├── semantic_meta.py    # OpenGraph/rel="tag" <head> tag builders + SPA shell injection
│   ├── routes/             # Route modules (one file per resource)
│   │   ├── auth.py         # Login, registration, token refresh, password reset
│   │   ├── sessions.py     # List and revoke active refresh-token sessions
│   │   ├── users.py        # Public user profiles, directory, per-user activity feed; authenticated profile updates
│   │   ├── profile_pages.py # Browser SPA profile routes, rel="me" link injection, ActivityPub negotiation
│   │   ├── activities.py   # Activity interactions (like, edit, delete, …)
│   │   ├── statuses.py     # Standalone status composer endpoint (POST /statuses)
│   │   ├── artists.py
│   │   ├── albums.py
│   │   ├── tracks.py
│   │   ├── playlists.py
│   │   ├── libraries.py
│   │   ├── favorites.py
│   │   ├── tags.py     # Global tag browsing and admin deletion
│   │   ├── genres.py       # Global genre browsing and admin deletion
│   │   ├── history.py      # Listening history
│   │   ├── radios.py       # Dynamic radio generation
│   │   ├── files.py        # Generic file upload/download (StoredFile)
│   │   ├── shares.py       # Share grants (owner→specific user) + /shares/mine listing
│   │   ├── share_urls.py   # Share URL tokens (revocable short links)
│   │   ├── share.py        # Public token resolver (redirects + sets cookie)
│   │   ├── reports.py      # Content moderation reports + admin review
│   │   ├── feeds.py        # RSS/Atom feeds under /feeds (users, entities, tags, genres)
│   │   ├── federation.py   # ActivityPub object endpoints (tracks, objects, federated music entities) + WebFinger
│   │   ├── remote.py       # Explicit remote lookup/dereference + cached remote objects
│   │   ├── admin.py        # Admin endpoints (settings, stats, user management)
│   │   ├── external_libraries.py # User external library CRUD, sync, tracks
│   │   └── admin_external_libraries.py # Admin external library management
│   └── middleware/
│       ├── auth.py         # JWT decode middleware + access-token helpers
│       ├── csrf.py         # Double-submit CSRF check for cookie-authenticated unsafe requests
│       ├── head.py         # HEAD answered as bodyless GET (crawler probes, e.g. og:image)
│       ├── media_cors.py   # Wildcard CORS on read-only media endpoints (federation embeds)
│       ├── rate_limit.py   # Redis sliding-window rate limiting (IP / user)
│       └── proxy.py        # X-Forwarded-Proto scheme handling behind reverse proxies
├── migrations/             # Alembic database migrations
│   ├── env.py              # Migration environment (imports all models)
│   ├── script.py.mako      # Template for generated revisions
│   └── versions/           # Revision scripts
├── models/                 # SQLAlchemy mapped models + shared enums
│   ├── base.py             # DeclarativeBase, UUID PK, timestamps, async session factory
│   ├── _enums.py           # Visibility enum (private / mentioned / local / followers / public)
│   ├── activity.py         # Activity, ActivityMention, ActivityTarget (federation interaction layer)
│   ├── preview_card.py     # PreviewCard — per-URL OpenGraph/<title>/domain card cache
│   ├── remote_object.py    # RemoteObject — cached remote AP objects + normalized fields
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
│   ├── follow.py           # Outbound follow relationships (local users → local/remote actors)
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
│   ├── output_stream.py    # Server-side audio output (encrypted provider config)
│   ├── playback_session.py # Persisted playback control plane for server outputs
│   └── setting.py          # Runtime-editable instance settings (key/JSON-value)
├── services/               # Business logic layer
│   ├── acl.py              # Three-level visibility + share-grant + share-token ACL
│   ├── activities.py       # Activity domain service (entity resolution, creation, interactions)
│   ├── auth.py             # User lookup, password hashing, session helpers
│   ├── audit.py            # Audit log helpers
│   ├── deletion.py         # Cascade deletion + activity retraction fan-out
│   ├── email.py            # SMTP email (verification, password reset)
│   ├── federation.py       # Actor provisioning, domain allow/block, inbox dispatch
│   ├── feeds.py            # RSS 2.0/Atom feed documents + per-entity feed queries
│   ├── follows.py          # Outbound follow/unfollow, decision folding, follow listings
│   ├── genres.py           # Genre validation, association and listing
│   ├── import_.py          # Import pipeline orchestration
│   ├── mentions.py         # @handle extraction, local/WebFinger resolution, safe HTML rendering
│   ├── remote_content.py   # Remote lookup parser, actor/object dereference + cache, cached search
│   ├── metadata.py         # Tag extraction coordination
│   ├── music.py            # Music library helpers
│   ├── musicbrainz.py      # MusicBrainz + Cover Art Archive enrichment (async httpx)
│   ├── outputs.py          # Output CRUD, config encryption, mount resolution
│   ├── playback.py         # PlaybackSession state machine + worker commands
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
│   ├── fetch.py            # SSRF-guarded remote fetch (DNS/IP checks, redirect revalidation)
│   ├── actors.py           # Actor document generation, federation storage helpers
│   ├── activities.py       # Activity creation (Create, Update, Delete, etc.)
│   ├── incoming.py         # Materialize inbound remote replies into Activity rows
│   ├── notifications.py    # Inbox recipient resolution + notification hooks
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
│   ├── mount.py            # Tornado listener handler for native HTTP mounts
│   └── transcoder.py       # ffmpeg wrapper: MP3, OGG, FLAC, AAC, Opus
├── streams/                # Server-side audio outputs
│   ├── base.py             # AudioOutput provider base class (fields, redaction)
│   ├── driver.py           # OutputDriver runtime interface (start/pause/seek/events)
│   ├── registry.py         # provider_type → AudioOutput registry
│   ├── icecast.py          # Icecast provider: ffmpeg → icecast:// pipeline
│   ├── http.py             # Native HTTP provider: ffmpeg → Redis Stream fan-out
│   ├── snapcast.py         # Snapcast provider: ffmpeg PCM → snapserver FIFO/TCP
│   ├── fake.py             # In-memory provider for tests
│   ├── types.py            # AudioSource and related dataclasses
│   └── worker.py           # `songhive stream-worker` session driver loop
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
│   ├── preview_cards.py    # Link-preview fetch + per-URL cache task
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
from songhive.streaming.mount import StreamMountHandler

fastapi_app = create_app(config)
wsgi_app = ASGIMiddleware(fastapi_app)
container = WSGIContainer(wsgi_app, executor=ThreadPoolExecutor(...))

tornado_app = Application([
    (r"/ws/events", EventWebSocket),
    (r"/ws/", EventWebSocket),
    (r"/api/v1/stream/(?P<track_id>[^/]+)", StreamHandler),
    (r"/streams/(?P<mount>[^/]+)", StreamMountHandler),
    (r".*", FallbackHandler, {"fallback": container}),
])

server = HTTPServer(tornado_app)
server.listen(config.server.port)
```

> **Note:** `WSGIContainer` must be given an explicit `ThreadPoolExecutor`.
> Without one (Tornado < 7 default) the WSGI app runs on the Tornado event
> loop thread, serializing every request — and any server-side remote fetch
> that triggers a synchronous call-back to this instance (e.g. a remote
> ActivityPub server resolving our `keyId` for HTTP signature verification)
> deadlocks, since the inbound request can never be served while the loop is
> busy inside the outbound lookup.

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
| `federation`   | enabled, instance_domain, instance_name, contact_name/contact_email/contact_url, private_key_path, allow/block lists, remote_search_access, fetch_timeout_seconds, remote_activity_retention_days, remote_activity_prune_schedule |
| `auth`         | registration_mode, secret_key, token TTLs, rate_limit, trusted_proxy_hops, cookie_secure, cookie_samesite, cookie_domain |
| `email`        | smtp_host, smtp_port, smtp_user, from_address, tls settings   |
| `musicbrainz`  | enabled, user_agent, cover_art, artist_image settings       |
| `notifications`| retention_days, purge_hour, digest_hour                     |
| `imports`      | scan_roots, bulk_import_sync_threshold                        |
| `streaming`    | max_bitrate, max_bitrate_by_role, default_bitrate, chunk_size, transcode_cache_enabled |
| `streams`      | enabled, allow_user_created_outputs, allowed/denied_user_providers, allowed_output_hosts, icecast_ffmpeg_path, worker timings, idle timeout, http_stream_* |

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
`track`, `album`, `artist`, `playlist`, `library`, `user` (the `user` entity
hosts standalone statuses posted through `POST /api/v1/statuses`), `remote`
(remote objects dereferenced on demand — see below). Each row
tracks its origin via `source_type` (`local` or a remote source),
`source_actor`, and `source_id` (unique per source), with `local_object_id`
as an optional canonical local identifier. Activities support threading
through `in_reply_to_activity_id`, arbitrary JSON `payload`s, source text vs
rendered content (`content_source` / `content` / `content_type` — plain text
or Markdown), an optional BCP-47 `language` tag mirrored into the object's
`contentMap`, and soft deletion (`deleted_at`, `retracted`). An activity's
`visibility` must not exceed its parent entity's visibility
(`Visibility.can_contain`).

`ActivityTag` rows link `Activity` and `Tag`, populated whenever an activity
contains hashtags (`#tag`) in its `content_source`/`content`. They enable
`GET /api/v1/tags/{tag}/activities`, which returns tag-matching activities
subject to the same visibility and entity ACL rules as other activity feeds.
The same rows are folded into the tag listing queries in
`services/tags.py`, so `GET /api/v1/tags/` and `GET /api/v1/tags/{tag}`
also surface hashtags that appear only on activities (as `activity` items),
counted by `published_at` and filtered by activity visibility plus the
containing entity's ACL.
Remote ActivityPub content is processed by Pubby and is mostly not
materialized as local `Activity` rows — the exception is inbound `Create`
replies, which `federation/incoming.py` stores as `source_type="remote"`
rows (see below) so their hashtags and mentions are associated too. Other
remote content stays interaction-only, so hashtags from remote posts cannot
be associated on the Songhive side unless Pubby stores or forwards them.
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
text is escaped and linkified by `pubby.render_post_html` (which also
converts newlines to `<br>`, since remote servers render `content`/`summary`
as HTML and would collapse literal newlines) — and
`process_mentions` is the single entry point returning resolved mentions,
rendered HTML, tags, and ActivityPub `Mention`/`Hashtag` tags. It accepts a
`content_type`: `text/plain` uses the escaped plain-text renderer described
above, while `text/markdown` (`render_mentions_markdown`, the default for new
statuses) runs the source through `mistune` — with raw HTML escaped, bare
URLs linkified, `javascript:`-style link targets neutralized — before the
same mention/hashtag linkification applies; handles inside code spans are
left untouched.
`ActivityTarget` rows track per-inbox outbound delivery state (`pending`,
`sent`, `failed`, `skipped`) with `attempts` / `last_error` /
`last_attempt_at` bookkeeping. Tracks already published to the fediverse
(`federation_object_id` set) are backfilled as `create` activities by
migration `4adb5fbea9d6`.

Preview cards attach link-preview metadata to activities: when a post is
created locally (`create_status`, `reply_to_activity`, `quote_activity`,
`record_track_publication`) or materialized from a remote `Create`,
`schedule_preview_card_fetch` enqueues `tasks/preview_cards.py`'s
`fetch_preview_card`, which runs `services/preview_cards.py`'s
`process_activity_preview_card`. The service picks the first *pure* URL —
Mastodon-style — from the raw `content_source` of local posts (rendered
mention/hashtag anchors never appear there) or from the anchors of the
remote HTML `content`, skipping `mention`/`hashtag`/`u-url` classes and
`rel="tag"` links, then fetches OpenGraph metadata with `<title>` and
finally the URL's domain as fallbacks. Fetched cards live in the
`preview_cards` table keyed by normalized URL and are shared across
activities; a cached card is reused while fresher than
`PREVIEW_CARD_MAX_AGE` (24h) and re-fetched at post time otherwise, so
views never trigger network fetches. Activities whose object carries
attachments get no card, and edits re-run the pipeline (`force=True`) so a
stale link is refreshed or cleared. Fetches are guarded against SSRF —
only `http(s)` URLs resolving to globally routable addresses are
requested, redirects are re-validated per hop, and bodies are capped at
`MAX_DOCUMENT_BYTES` (1 MiB). The pipeline is gated by the instance-level
`preview_cards_enabled` runtime setting (admin UI, default on) and by the
per-user `preview_cards_enabled` preference (profile form, default on);
either opting out skips the enqueue, and the task re-checks both gates so
rows created before a change are honoured. `ActivityResponse.preview_card`
serializes the linked card and `ActivityCard.vue` renders it below the
post content.

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
authenticated user; `mentioned` requires the owner or a mentioned user;
`private` is owner-only; retracted activities are never viewable). Admins
get no bypass — `mentioned` and `private` activities stay confined to
their audience for every viewer. The same rules are enforced in SQL by
`_activity_visibility_filter` in `services/acl.py` (feed/tag/reply
listings) and in
`reply_count` computation, so restricted replies neither render nor leak
through counters for viewers outside their audience. Interactions are layered on top of `create_local_activity`:
`like_activity` records an idempotent `like` (400 on a duplicate, 404 on a
retracted target) that inherits the target's visibility and stores a
`federation/activities.create_like_activity` `Like` payload whose `to`/`cc`
come from `activity_audience`. `boost_activity` mirrors it as an
`announce` activity carrying an `Announce` payload
(`federation/activities.create_announce_activity`, a thin wrapper over
pubby's `build_announce_activity`) whose `object` is the target's
`source_id`; duplicates are rejected the same way. `reply_to_activity`
stores a `reply` activity whose payload is a `Create(Note)` with
`inReplyTo` set to the target's `source_id` — the reply reuses the status
composition pipeline (`process_mentions`, media/track attachment
resolution, `contentMap` language tagging) and its requested visibility
may never exceed the target's (`Visibility.can_contain`) or the entity's.
The replied-to author is always addressed: a local owner gets an
`ActivityMention` row plus a `reply` notification (`_notify_reply`), a
remote author gets a `Mention` tag so `resolve_audience` delivers the
reply to their inbox. Interactions skip the `can_manage` gate
(`require_manage=False`) — view access on the target is enough — while
content-producing activity types still require manage rights. The
`POST /api/v1/activities/{id}/like`, `/{id}/boost`, `/{id}/reply` and
`/{id}/quote`
endpoints perform the 404/403/400 (or 422 for invalid replies) checks,
provision the author's actor keys (`ensure_user_actor`), commit, then fan
out: likes and boosts share `_fan_out_reaction_activity` (exposed as
`fan_out_like_activity`/`fan_out_boost_activity`), which — for remote
targets — resolves the reacted author's inbox via
`services/federation.resolve_actor_inbox` (the `federation_actor_cache`
table first, then a signed actor-document fetch, preferring `sharedInbox`)
and hands it to `fan_out_activity` alongside the reaction's own audience;
replies fan out inline through `fan_out_activity`, best-effort. Quotes
(`quote_activity`) work like replies — a `Create(Note)` row linked to the
quoted activity through `in_reply_to_activity_id` — but the note carries
the FEP-0449 `quote` field plus the `quoteUri`/`quoteUrl` forms Fedibird,
Akkoma and Mastodon read and Misskey's `_misskey_quote` (never
`inReplyTo`: a quote is not a reply), the quoted author is always
addressed via a `Mention` tag — and `activity_audience` puts mentioned
actors in `to` for public and followers posts, matching how Mastodon and
Akkoma address their own — and FEP-044f authorization
is negotiated: quoting a local user's post self-issues the
`QuoteAuthorization` their auto-approval would grant (stored so it
dereferences, stamped as `quoteAuthorization` on the note), while quoting
a remote post delivers a `QuoteRequest` — quoting `Note` as `instrument`,
quoted object as `object` — to the remote author's inbox
(`_deliver_quote_request`, sent before the `Create` fan-out so the
authorization flow starts first). The remote `Accept` answering it stamps the
issued authorization id onto the stored quote (see inbound
materialization below). `DELETE /api/v1/activities/{id}/like` and
`/{id}/boost` retract the
caller's reaction: `unreact_activity` soft-deletes the like/announce row
(so the target can be reacted to again), removes the notification it
produced, and returns the retracted activity; `fan_out_unreaction_activity`
then delivers an `Undo` wrapping the originally federated `Like`/`Announce`
(`federation/activities.create_undo_activity`, over pubby's
`build_undo_activity`) to exactly the inboxes recorded as `sent` for the
reaction. Unreacting a target without a live reaction returns 404.

Every `ActivityResponse` carries interaction state resolved by
`resolve_interaction_summaries`: `like_count` and `boost_count` combine
direct local `Activity` rows (interactions stored with
`in_reply_to_activity_id` pointing at the target) with confirmed remote
interactions recorded in Pubby's `federation_interactions` storage against
the activity's `source_id` (`_remote_interactions`, thread-offloaded and
best-effort). `reply_count` instead covers the whole sub-thread: every
non-deleted `reply` descendant reachable through the `in_reply_to` chain —
computed with the `_activity_descendants_cte` recursive CTE — plus remote
replies targeting any node in it, including remote replies to remote
replies reached breadth-first through `object_id` chains
(`_remote_thread_interactions`). `quote_count` is flat — quotes attach to
the quoted post itself, never to each other — and counts direct
`quote`-type children (local quotes and materialized remote ones) plus
confirmed remote `QUOTE` interactions, deduplicated on `object_id`.
Remote replies already materialized into
`Activity` rows are skipped so a reply backed by both a row and a stored
interaction counts once. Descendants the requester may not see
(`mentioned` replies naming someone else, `private` rows owned by another
user) are not counted, though traversal still crosses them to reach their
visible children. `liked`/`boosted` report the requester's
own live like/announce rows, and `can_interact` is false for activity types
that cannot themselves be reacted to (`like`, `announce`, `delete`).
`ActivityResponse` also carries `object_url`/`object_type`, resolved from
the payload's `object`: `Create`-style activities expose the embedded
document's `id`/`type`, while `Like`/`Announce` payloads reference the
reacted object as a bare id — which is exactly the target a reaction card
should link to — and expose no `object_type`. `PATCH` rejects like and
announce activities (422): a reaction's object is a bare reference and
its visibility is inherited, so there is nothing editable. `GET
/api/v1/activities/{id}/likes` and `/{id}/boosts` return the
known interactors — local users resolved to profiles, remote actors from
Pubby's interaction records — via `list_activity_interactors`, and
`GET /api/v1/activities/{id}/replies` returns the known replies via
`list_activity_replies`: visibility-filtered local `reply` activities —
every descendant in the thread, not just direct children — serialized as
full `ActivityResponse`s plus remote replies rebuilt from the `raw_object`
metadata the inbox processor stored, each carrying `in_reply_to` (the
replied-to object id) so clients can regroup the flat list into threads.
Replies already materialized into `Activity` rows are filtered out of the
remote list so they render only as full cards. `GET
/api/v1/activities/{id}/quotes` lists the known quotes via
`list_activity_quotes`: visibility-filtered local `quote` activities —
locally authored ones and remote quotes materialized into `Activity`
rows — serialized as full `ActivityResponse`s, plus remote quotes rebuilt
from Pubby `InteractionType.QUOTE` records keyed by the quoted object's
`source_id`, each carrying `quoted` (the quoted object id) instead of the
reply's `in_reply_to`. Materialized remote quotes are filtered out of the
remote list so they render only as cards.
The frontend renders them Mastodon-style: every descendant of a direct
reply is unfolded flat into that reply's thread, each thread marked by its
own vertical line. All four listings gate on `can_view_activity` and allow
anonymous reads of public targets.
`GET /api/v1/activities/{id}` returns a single activity under the same
rules — it backs the notification UI's embedded activity cards and the
SPA's `/activities/:id` permalink page (`views/ActivityView.vue`), which
also lists the known quotes (`GET /{id}/quotes`) under the card — local
ones as `ActivityCard`s, federated-only ones as `ActivityRemoteReply`
rows — failure-isolated so a listing error cannot sink the page.
`ActivityCard.vue` carries a separate Quote action next to Reply: a
`StatusComposer` posts the quote through `POST
/api/v1/activities/{id}/quote`, the `quote_count` button toggles a flat
chronological list of local and remote quotes, and a quote card itself
embeds the quoted post via `ActivityObjectEmbed`.
`GET /api/v1/activities/lookup?url=...` resolves an ActivityPub object URL
to the same `ActivityResponse`: it matches `Activity.source_id` directly
(covering materialized remote replies whose object ids live on remote
paths) and, for `{actor}/objects/{uuid}` shapes, `local_object_id`. Browser
requests to a local `/users/{name}/objects/{id}` that map to a stored
activity are redirected by `federation.py` to the `/activities/{id}`
permalink, while ActivityPub `Accept` headers still get the object JSON.
Notification payloads denormalize `object_activity_id`/`object_type`/
`object_page_url` for the note itself and `target_object_*` for the
replied-to/quoted activity, so clients can render real activity cards and
link replies to their parent instead of a remote object id that does not
dereference to a page.

`resolve_audience` maps an activity's visibility to the set of remote
**inbox URLs** it should reach: `public` and `followers` activities go to
the author's follower inboxes (`services/federation.get_follower_inboxes`
reads Pubby's `federation_followers` storage via `pubby.collect_inboxes`,
preferring `shared_inbox` and deduplicating) plus every remote mentioned
actor, while `mentioned` activities reach only the mentioned actors.
`public` activities additionally reach followers of objects in the
reply chain — remote actors can `Follow` a local object rather than an
actor (Friendica sends `Follow` on a thread's root item for conversation
subscriptions; see *Object follows* below), and
`services/federation.get_object_follower_inboxes` collects the inboxes
subscribed to the activity's own object id or any of its
`in_reply_to_activity_id` ancestors.
Mentions that resolved to a local user (`user_id` set) or an actor URL on
the local instance domain have no remote inbox and are excluded, as are
non-HTTP(S) actor URLs; `private` and `local` never federate. Mentioned
inboxes are resolved concurrently through `resolve_actor_inbox`, signed
with the owner's key.

`fan_out_activity` is the delivery step: it unions the resolved audience
with caller-supplied `extra_inboxes`, drops inboxes already booked, and
creates an `ActivityTarget` row per remaining inbox before enqueueing
`tasks.federation.deliver_activity` (which applies the federation gate, the
blocked-domain gate, and exponential-backoff retries). Targets are marked
`sent` once the task accepts the job, `failed` with `last_error` when
enqueueing raises, and `skipped` — without an attempt — for blocked or
non-allowed inbox domains; `pending` remains the default for rows staged
without a dispatch attempt. Fan-out no-ops for remote or retracted
activities, missing payloads, non-federating visibilities, disabled
federation, and owners without a signing key.

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

`PATCH /api/v1/activities/{id}` handles author edits behind an
`acl.can_manage` check on the containing entity (owner or admin — the same
gate `create_local_activity` applies to content-producing types). Only
fields present in the request body change: a `content` edit goes through
`services.activities.update_activity`, which re-runs the
`process_mentions` pipeline on the new `content_source` (honoring the
stored or newly supplied `content_type`): `content` is re-rendered as
mention-aware safe HTML, the `activity_mentions` rows are replaced with
the newly resolved set, and — when the stored `payload` embeds a dict
`object` — the object's `content` and `tag` are rebuilt
(`pubby.set_object_content` merges tags while preserving pre-existing
tags, then the pipeline's `Mention` tags and HTML are layered on) and it
is stamped with `updated`. `language` replaces the activity's BCP-47 tag
(`null` clears it) and rewrites or drops the object's `contentMap`.
`media_ids`/`track_ids` rebuild the object's `attachment` list: docs
stamped with `songhive:fileId`/`songhive:trackId` (emitted by
`stored_file_to_attachment`/`track_to_attachment`) are user-managed and
replaced wholesale, while unmarked entity-owned docs — e.g. a shared
track's own `Audio` attachment — are preserved; the same ownership,
access, visibility-escalation and share-grant rules as status creation
apply, evaluated against the post-edit visibility. A `visibility` edit
goes through `cascade_visibility_update` so already-delivered inboxes
receive an `Update` or a `Delete(Tombstone)`. Content edits then federate
through `services.activities.fan_out_activity_update` — invoked after both
edits so the final visibility applies — which wraps the rebuilt object in
an `Update` (`federation.activities.create_object_update_activity`, with
`to`/`cc` rewritten to the current audience) and delivers it via
`deletion.enqueue_activity_delivery` to every inbox recorded as `sent`.
`fan_out_activity` then re-delivers the stored payload to inboxes first
reached by the edit (e.g. a newly mentioned actor), so those recipients
get the `Create` carrying the updated object rather than an `Update` for
an object they have never seen. The fan-out no-ops for remote or
retracted activities, payloads without an embedded dict `object`,
non-federating visibilities, disabled federation, and owners without a
signing key.

`GET /api/v1/{entity_type}/{entity_id}/activities` (a second router in
`api/routes/activities.py`, mounted at `api_prefix`) is the public read
endpoint. It validates `entity_type` against `ACTIVITY_ENTITY_TYPES`
(400), resolves the entity (404), and gates on `acl.can_access` (403) —
anonymous requesters may read publicly accessible entities, matching the
other read endpoints; share tokens are not honored, consistent with the
like/edit routes. `services.activities.list_activities` then applies the
same per-activity visibility rules as `can_view_activity` in SQL
(`_activity_visibility_filter`) so pagination cannot leak or under-fill
pages, supports `activity_type`/`source_type` filters, and
keyset-paginates on `(published_at, id)` newest-first with an opaque
base64url cursor (`limit` 1–100, default 20; malformed cursors return
400). Responses are serialized through
`ActivityResponse`/`ActivityListResponse`: the raw `payload`,
`retracted`, and `deleted_at` stay internal while resolved mentions are
included.

Deletion is handled by `services/deletion.py`'s `cascade_delete_entity`,
which is invoked from every entity delete path (track, album, artist,
playlist, library). Local activities are soft-deleted (`deleted_at` set)
and a `Delete(Tombstone)` is enqueued through `deliver_activity` for every
inbox recorded as `sent` in `activity_targets`, signed with the activity
owner's private key; remote activities are hard-deleted without fan-out.
Single-activity retraction follows the same rules through
`services/activities.retract_activity`, exposed as `DELETE
/api/v1/activities/{id}` behind `acl.can_manage` on the containing entity —
except that retracting a `like`/`announce` enqueues an `Undo` wrapping the
originally federated reaction payload (matching the `unreact_activity`
path) instead of a `Delete(Tombstone)`, since `Delete` is not the
ActivityPub way to retract a reaction, and remote activities are
soft-deleted rather than removed: the remote instance owns retraction, and
the surviving row keeps the stored Pubby interaction deduplicated so the
reply cannot resurface.

Track fediverse publications are recorded as activities too: every publish
path — the manual `POST /api/v1/tracks/{id}/publish`, uploads and imports
of public tracks that opt in with `publish=true` (including the
`process_upload` Celery task and bulk library uploads), and entity
visibility transitions to `public` — calls
`services/activities.record_track_publication`. Uploads are local-only by
default: without the flag the track gets no `federation_object_id` and no
publication activity. `record_track_publication` builds a `Create`
envelope around one of two object shapes, selected by `object_type`:
`audio` (the canonical publication — the `Audio` object built on the
track's freshly minted `federation_object_id`) or `note` (a share whose
`content` renders as the post body on every remote server and whose
`Audio`-typed attachment links back to the track's published object; a
fresh object id is minted per share). It stores the payload on a `create`
activity whose `source_id` matches the published object URL
(`{actor_url}/objects/{object_id}`), keeps the
one-off `status` post text in `content_source`, persists its resolved
mentions as `activity_mentions` rows, records the caller-selected
`visibility` (default `public`), and delivers through
`fan_out_activity` so each reached inbox is booked in `activity_targets`
and later retraction reaches exactly those inboxes. When a public track
goes private, `retract_track_publications` soft-deletes all the live
publication rows — both the canonical `Audio` and any `Note` shares —
delivering a `Delete(Tombstone)` per publication `source_id` through
`retract_activity`, alongside the track-level
`unpublish_track_activity` `Delete(Tombstone)`. Metadata edits on a
published track — `PATCH /api/v1/tracks/{id}` touching `title`,
`artist_name`, `description`, or `genre` — re-sync the stored object
through `services/activities.sync_track_publications`: each live local
`create` activity's `payload.object` is rebuilt from
`track_to_audio_object` or `track_to_note_object` depending on the stored
object `type` (keeping the object id stable — and the `published` stamp
for `Note` shares — while stamping
`updated`), a one-off `status` kept in `content_source` is re-applied as
the post body so the track description does not clobber it, and the
result fans out via `fan_out_activity_update` so delivered inboxes get an
`Update` carrying the refreshed object.
`get_activity_unpublish_info` returns the `ActivityUnpublishInfo`
(activity id, `source_id`, `actor_url`, sent inboxes) used for that
delivery, and `federation/activities.py` builds the `Delete(Tombstone)`
payload via `create_tombstone_delete_activity` — a thin adapter around
`pubby.build_delete_activity` (0.3.2) that keeps a plain string `@context`.

Standalone statuses — posts not attached to any media entity — are created
through `POST /api/v1/statuses/`, which calls
`services/activities.create_status` after provisioning the author's actor
(`ensure_user_actor`). The service records a `create` activity on the
author's `user` entity, so statuses show up in the author's profile posts
feed (`GET /api/v1/users/{username}/activities?mode=posts`) and can be
edited/retracted through the regular activity endpoints. Posts mode
returns the user's local `create` and `quote` activities plus —
Mastodon-style — their `announce` boosts unless `include_boosts=false`,
and their `reply` activities only with `include_replies=true` (replies
are hidden by default); `mode=all` returns every authored activity and
ignores the include flags. The request
carries the raw `status` source text (rendered through `process_mentions`
as `text/markdown` — the default — or `text/plain`), a `visibility`, an
optional BCP-47 `language` (validated, stored on the activity, mirrored
into the Note's `contentMap`), and two attachment sets: `media_ids`
reference previously uploaded `StoredFile`s (serialized by
`federation/serializers.stored_file_to_attachment`; the service escalates
each file's visibility to what the status audience requires — never
downgrades — and grants `file` shares to mentioned local users on
`mentioned`-visibility posts) and `track_ids` reference hosted tracks the
author may access (serialized by `track_to_attachment` as `Audio`
attachments embedding the stream URL, or `Document` links for tracks
without audio). Audio attachments — on statuses and on the `Audio`/`Note`
track objects alike — additionally carry the namespaced
`songhive:trackId`, `songhive:trackTitle`, `songhive:artistName`,
`songhive:albumName` and `songhive:trackUrl` keys plus a standard `image`
entry resolving cover art (track image, then album cover file, then the
album's remote `cover_url`, then the artist's image file or remote
`image_url`), so music-aware consumers can render a rich
player without parsing the flat `name` label; remote servers ignore
unknown keys. Each category is capped at four attachments, and a status
may be attachments-only. The resulting `Create(Note)` is built by
`federation/activities.create_status_activity` and fanned out through
`fan_out_activity` exactly like entity activities: it federates to the
visibility audience when federation is enabled, and stays local otherwise.
The manual track publish endpoint accepts the same `content_type`,
`language`, and `media_ids` fields, so the composer can also drive
`POST /api/v1/tracks/{id}/publish`. Statuses, replies, quotes, activity
edits and track publications also accept an `audio_import` object
(`upload_to_library`, default `true`; `fetch_metadata`, default `false`;
`library_id`, default the author's lazily-created private "Uploads"
library): when enabled, each `audio/*` file attachment is imported into
the target library as a track after the post is saved, via
`plan_audio_import`/`apply_audio_import` (`api/routes/files.py`) and
`services/import_.import_stored_audio_files`, which reuses the file's
existing `Track` when it already backs one of the author's tracks and
treats external duplicates as `keep_local`. The import plan is validated
before the activity fans out so a bad `library_id` fails the request
without enqueued deliveries; per-file import failures are logged and
skipped so the post always succeeds. To keep the choice effective at
post time, the composer uploads browser files with
`import_audio=false`, which stores `audio/*` uploads as plain
`StoredFile`s (still audio-hashed, so the post-time import dedupes)
instead of auto-importing them. Users pick their default post format
through the `status_content_type` profile field (`PATCH
/api/v1/users/me`), defaulting to `text/markdown`.

`GET /users/{username}/objects/{object_id}` in `api/routes/federation.py`
is the dereference endpoint for federated objects. Public tracks still
resolve through `Track.federation_object_id` to their `Audio` object; when
no track matches, an `Activity` is resolved by `local_object_id` or
`source_id`, scoped to the requested user (`owner_user_id`), so an object
is only served under its owner's namespace. Soft-deleted activities answer
with a `Tombstone` object (`federation/activities.build_tombstone_object`)
whose shape mirrors the object embedded in `Delete(Tombstone)` deliveries.
Live activities are only served when their visibility federates
(`Visibility.federates`: `mentioned`, `followers`, `public`) —
`private`/`local` objects were never distributed and answer 404.
`federation/activities.build_activity_object` produces the document: the
stored `payload` verbatim for payload-bearing activities (e.g. `Like`) —
except `Create` envelopes, whose embedded object is served instead because
the activity's `source_id` identifies the published object, not the
envelope — or a synthesized `Note` carrying the rendered `content`, the
`activity_audience`-derived `to`/`cc`, `Mention` tags, and `inReplyTo`.
Every post object — `track_to_audio_object`/`track_to_note_object`
serials, status and reply `Note`s, and payload-derived documents at serve
time — is stamped by `pubby.allow_public_quotes` with
`interactionPolicy.canQuote` granting `automaticApproval` to `as:Public`
(FEP-044f): quoting is always allowed, so Mastodon enables its Quote
action and auto-approves the quote once the `QuoteAuthorization` it
fetches matches. Restamping at serve time keeps objects published before
the policy existed quotable without rewriting stored payloads.
Because object URLs double as the objects' own `url` (a `Note` share's
permalink on remote servers), clients not accepting an ActivityStreams
media type are redirected to the SPA rather than served JSON: a
track-resolved object redirects to `/tracks/{id}`, an activity-resolved
object to `/{entity_type}s/{entity_id}/activities` (`libraries` for
`library`).

Served track objects are built by `_track_object_document`, which extends
the `track_to_audio_object` payload with a top-level `@context` and the
`public` `to`/`cc` audience — remote fetchers (e.g. Mastodon's URL lookup)
reject context-less documents, and an audience-less object would be
imported as a direct-only status. The object's `attributedTo` leads with
the publishing actor rather than the artist page URL because remote
importers take its first entry as the author and only actor URLs are
dereferenceable. `GET /tracks/{track_id}` content-negotiates on top of
that: requests accepting `application/activity+json`/`application/ld+json`
receive the same `Audio` document (the track page URL is the `text/html`
`url` advertised in every published object, so remote servers fetch it when
a user pastes the link into a search box), while browsers receive the SPA
shell annotated with a `Link: rel="alternate"` header and a
`<link rel="alternate" type="application/activity+json">` element pointing
at the object URL. The object is only served while the track is published
(`federation_object_id` set). When no `Audio` exists, ActivityPub fetches
are redirected (303 See Other) to the track's earliest live local `create`
activity — `_earliest_track_post` picks the oldest non-deleted,
federating-visibility row and the object route then serves that share's
`Note` document — so a URL search on an Audio-less track still resolves to
a post (remote fetchers like Mastodon follow the redirect and import the
redirected object under its own `id`). Tracks with neither a published
object nor live shares answer 404 so a remote fetch cannot resurrect a
retracted post under a different id.

`GET /activities/{activity_id}` applies the same content negotiation to
the SPA's activity permalink — the URL `ActivityCard` offers for copying —
so pasting it into a remote search box resolves too. ActivityPub fetches
of a local activity receive the same document the canonical object route
serves (`_activity_object_response`, shared with `get_object`), gated on
the owner being an active local user; remote-sourced activities redirect
(303 See Other) to their origin's object id, which stays authoritative.
Browsers receive the SPA shell annotated with the `Link`/
`<link rel="alternate">` discovery hints pointing at `source_id`, the
canonical object URL.

### Genres

Genres are stored in a dedicated `Genre` table and linked to `Track` and
`Album` through `GenreTrack` and `GenreAlbum` association tables. The free-text
`Track.genre` and `Album.genre` columns remain the source of truth for embedded
metadata round-trips, while the normalised tables enable browsing, counting, and
filtering. `GenreTrack.inherited` distinguishes album-inherited values from
explicit track-level overrides. `services/genres.py` validates names, manages
associations, propagates album genres down to tracks that have no explicit
genre of their own, and re-derives the album genre from the intersection of its
tracks' explicit genres. The tag system receives the same genre-derived
tags: the genre string is split and mapped to valid tag names so
genre-derived tags appear alongside user-created ones. The public API
exposes global genre listing and deletion in `api/routes/genres.py`, and
per-resource genre management is supported through `POST`/`DELETE` sub-routes on
tracks and albums as well as the `genre` field on track/album updates. The
frontend mirrors the tag browsing experience: `GenresView` and `GenreView`
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

#### S3 provider

The `s3` provider (`external/_s3.py`) indexes audio objects stored in an
S3-compatible bucket (AWS S3, MinIO, and other S3 API-compatible services).
Both regular users (when `allow_user_created_libraries` permits the provider)
and admins can attach buckets; like every external library, the backing
`Library` is private to its owner by default and can be made `public`/`local`
or shared explicitly through library share grants.

An S3 external library stores the following adapter config:

| Key                         | Required | Default     | Description                                                              |
|-----------------------------|----------|-------------|--------------------------------------------------------------------------|
| `bucket`                    | yes      | —           | Bucket containing the audio objects.                                     |
| `prefix`                    | no       | `""`        | Only index objects under this key prefix.                                |
| `endpoint_url`              | no       | AWS         | Custom endpoint for S3-compatible services (e.g. `http://minio:9000`).   |
| `region`                    | no       | —           | Bucket region.                                                           |
| `access_key`/`secret_key`   | no       | ambient     | Static credentials; when omitted the SDK's ambient credentials are used. |
| `path_style`                | no       | `false`     | Force path-style addressing (needed by some S3-compatible services).     |
| `presigned_urls`            | no       | `true`      | Redirect clients to short-lived presigned GET URLs for playback.         |
| `presigned_expiry_seconds`  | no       | `3600`      | Presigned URL lifetime, clamped to 60–604800 (SigV4's one-week limit).   |
| `extensions`                | no       | all audio   | List of object-key suffixes to index.                                    |
| `exclude`                   | no       | `[]`        | `fnmatch` patterns applied to prefix-relative keys.                      |
| `recursive`                 | no       | `true`      | When `false`, only index keys directly under `prefix` (delimiter `/`).   |
| `allow_hashing`             | no       | `true`      | Whether to compute audio hashes for new/updated objects.                 |
| `fast_hash`                 | no       | `false`     | Hash raw object bytes instead of ffmpeg audio-only hashing.              |
| `allow_write_tags`          | no       | `false`     | Rewrite embedded tags by re-uploading the object in place.               |
| `allow_rename_source`       | no       | `false`     | Allow `rename_source` (server-side copy + delete).                       |
| `allow_delete_source`       | no       | `false`     | Allow `delete_source` to remove objects.                                 |

Streaming defaults to presigned GET URLs returned with `safe_to_redirect`
(the client fetches bytes directly from the object store, including HTTP
ranges). When `presigned_urls` is `false`, Songhive proxies the object body
through the normal stream handler, so bandwidth flows through the server.
Hashing prefers the object's S3 `ChecksumSHA256` attribute when present,
then falls back to `fast_hash` streaming or a temp-file ffmpeg pass —
all gated on `allow_hashing` because they require downloading the object.

#### S3 freshness vs. filesystem watching

The filesystem watchdog only applies to the `local` provider — object stores
have no `watchfiles`-style change events, so S3 libraries rely on scheduled
syncs (`sync_interval_seconds`, driven by `scan_scheduled_syncs_task`).
To keep polling cheap, `iter_items` lists the bucket prefix with
`ListObjectsV2` (paginated, no object payloads) and reports each object's
`ETag`, `LastModified`, and size. `external/sync.py` advertises this through
the `detect_changes` capability: when the stored `provider_etag` (or
mtime+size) still matches, the item is treated as unchanged and sync skips
metadata reads, hashing, and downloads entirely. A scheduled sync over an
unchanged bucket therefore costs one `ListObjectsV2` call set — no per-object
transfer — while still catching new, modified, and deleted keys on the next
interval. Operators wanting tighter freshness should shorten
`sync_interval_seconds` rather than run manual full scans; scoped syncs
(`scope=`) can restrict a sync to a sub-prefix. Future integrations could
plug S3 event notifications or S3 Inventory into the same task pipeline.

#### SFTP provider

The `sftp` provider (`external/_sftp.py`, asyncssh) indexes audio files on a
remote host reached over SSH/SFTP. **The remote host must be reachable from
the Songhive instance** — every listing, download, and mutation originates
server-side, so firewalls/NAT between Songhive and the SSH server must allow
outbound connections to the configured host and port. Like S3, both regular
users (when `allow_user_created_libraries` permits the provider) and admins
can attach SFTP roots.

An SFTP external library stores the following adapter config:

| Key                        | Required | Default  | Description                                                            |
|----------------------------|----------|----------|------------------------------------------------------------------------|
| `host`                     | yes      | —        | Hostname or IP of the SSH server.                                      |
| `port`                     | no       | `22`     | SSH port.                                                              |
| `username`                 | yes      | —        | SSH login user.                                                        |
| `password`                 | no       | —        | SSH password (stored encrypted).                                       |
| `private_key`              | no       | —        | PEM/OpenSSH private key (stored encrypted).                            |
| `private_key_passphrase`   | no       | —        | Passphrase for an encrypted `private_key` (stored encrypted).          |
| `verify_host_key`          | no       | `true`   | Verify the server host key against `known_hosts` or the default files. |
| `known_hosts`              | no       | —        | Inline OpenSSH `known_hosts` lines used when `verify_host_key` is on.  |
| `root`                     | no       | `.`      | Remote directory to index; relative paths resolve against the login home. |
| `connect_timeout`          | no       | `15`     | Seconds to wait for TCP connect and SSH login.                         |
| `extensions`               | no       | all audio| List of file extensions to index.                                      |
| `exclude`                  | no       | `[]`     | `fnmatch` patterns applied to root-relative paths.                     |
| `recursive`                | no       | `true`   | Whether to scan subdirectories.                                        |
| `follow_symlinks`          | no       | `false`  | Whether to follow symbolic links while scanning.                       |
| `allow_hashing`            | no       | `true`   | Whether to compute audio hashes for new/updated files.                 |
| `fast_hash`                | no       | `false`  | Hash raw file bytes instead of ffmpeg audio-only hashing.              |
| `allow_write_tags`         | no       | `false`  | Rewrite embedded tags by re-uploading the file in place.               |
| `allow_rename_source`      | no       | `false`  | Allow `rename_source` to rename remote files.                          |
| `allow_delete_source`      | no       | `false`  | Allow `delete_source` to remove remote files.                          |

Authentication accepts a password, an inline private key (optionally
passphrase-protected), or both — asyncssh presents whichever the server
accepts. With neither configured, the Songhive process's default SSH client
keys and agent are tried, mirroring the S3 adapter's ambient-credentials
behaviour.
`verify_host_key` defaults on and checks the host key against the inline
`known_hosts` data or the process's `~/.ssh/known_hosts`; disabling it must be
an explicit `verify_host_key = false`, since an unverified connection is open
to MITM impersonation (including credential theft when password auth is used).

SFTP has no presignable URL, so `open_stream` always returns a proxied byte
iterator (with byte-range support via positioned reads). Freshness follows
the S3 scheduled-sync model: `iter_items` walks the remote tree with
`READDIR` (metadata only) and reports an `mtime:size` change token via
`detect_changes`, so unchanged trees cost a directory walk and no file
transfer.

#### WebDAV provider

The `webdav` provider (`external/_webdav.py`, httpx) indexes audio files
stored on a remote WebDAV server (Nextcloud, ownCloud, nginx WebDAV, etc.).
**The server must be reachable from the Songhive instance** — every listing,
download, and mutation originates server-side, so firewalls/NAT between
Songhive and the WebDAV host must allow outbound HTTP or HTTPS connections.
Like S3 and SFTP, both regular users (when `allow_user_created_libraries`
permits the provider) and admins can attach WebDAV roots.

A WebDAV external library stores the following adapter config:

| Key                        | Required | Default  | Description                                                               |
|----------------------------|----------|----------|---------------------------------------------------------------------------|
| `url`                      | yes      | —        | Base WebDAV URL, e.g. `https://nextcloud.example.com/remote.php/dav`.     |
| `root`                     | no       | `""`     | Remote directory under the base URL to index.                             |
| `username`                 | no       | —        | HTTP Basic auth username.                                                 |
| `password`                 | no       | —        | HTTP Basic auth password (stored encrypted).                              |
| `token`                    | no       | —        | Bearer token sent in the `Authorization` header.                          |
| `verify_ssl`               | no       | `true`   | Verify TLS; set to `false` to disable or to a CA bundle path.             |
| `timeout`                  | no       | `30`     | HTTP request timeout in seconds.                                          |
| `extensions`               | no       | all audio| List of file extensions to index.                                         |
| `exclude`                  | no       | `[]`     | `fnmatch` patterns applied to root-relative paths.                        |
| `recursive`                | no       | `true`   | Whether to scan subdirectories.                                           |
| `allow_hashing`            | no       | `true`   | Whether to compute audio hashes for new/updated files.                    |
| `fast_hash`                | no       | `false`  | Hash raw file bytes instead of ffmpeg audio-only hashing.                 |
| `allow_write_tags`         | no       | `false`  | Rewrite embedded tags by re-uploading the file in place.                  |
| `allow_rename_source`      | no       | `false`  | Allow `rename_source` to rename remote files.                             |
| `allow_delete_source`      | no       | `false`  | Allow `delete_source` to remove remote files.                             |

Authentication accepts a `username`/`password` pair (HTTP Basic auth) or a
`token` (Bearer auth). `verify_ssl` defaults on and should only be disabled
for testing or trusted private networks, since disabling it leaves the
connection open to MITM impersonation and credential theft.

WebDAV has no presignable URL, so `open_stream` always returns a proxied byte
iterator with HTTP `Range` support. `iter_items` walks the remote tree with
WebDAV `PROPFIND` (metadata only) and reports an ETag/mtime/size change token
via `detect_changes`, so unchanged trees cost a directory walk and no file
transfer. As with S3 and SFTP, freshness comes from scheduled syncs rather
than filesystem watching.

#### Dropbox provider

The `dropbox` provider (`external/_dropbox.py`, httpx) indexes audio files
stored in a Dropbox account through the Dropbox HTTP API v2. All traffic
originates server-side, so the Songhive instance needs outbound HTTPS access
to `api.dropboxapi.com` and `content.dropboxapi.com` (plus
`dl.dropboxusercontent.com` when clients fetch temporary links directly).
Like the other providers, both regular users (when
`allow_user_created_libraries` permits the provider) and admins can attach
Dropbox folders.

A Dropbox external library stores the following adapter config:

| Key                        | Required | Default  | Description                                                               |
|----------------------------|----------|----------|---------------------------------------------------------------------------|
| `access_token`             | yes*     | —        | Dropbox OAuth access token (short-lived, ~4 hours).                       |
| `refresh_token`            | yes*     | —        | OAuth refresh token — the durable option; mints access tokens on demand.  |
| `app_key`                  | with `refresh_token` | — | App key of the Dropbox app the refresh token was issued to.        |
| `app_secret`               | no       | —        | App secret of the Dropbox app, if it has one (stored encrypted).          |
| `account_id`               | no       | —        | Dropbox account id, filled in by the OAuth connect flow.                  |
| `root`                     | no       | `""`     | Dropbox folder to index (e.g. `/Music`); empty indexes the account root.  |
| `timeout`                  | no       | `30`     | HTTP request timeout in seconds.                                          |
| `temporary_links`          | no       | `false`  | Redirect clients to short-lived `get_temporary_link` URLs for playback.   |
| `extensions`               | no       | all audio| List of file extensions to index.                                         |
| `exclude`                  | no       | `[]`     | `fnmatch` patterns applied to root-relative paths.                        |
| `recursive`                | no       | `true`   | Whether to scan subfolders of the root.                                   |
| `allow_hashing`            | no       | `true`   | Whether to compute audio hashes for new/updated files.                    |
| `fast_hash`                | no       | `false`  | Hash raw file bytes instead of ffmpeg audio-only hashing.                 |
| `allow_write_tags`         | no       | `false`  | Rewrite embedded tags by re-uploading the file in place.                  |
| `allow_rename_source`      | no       | `false`  | Allow `rename_source` to rename Dropbox files.                            |
| `allow_delete_source`      | no       | `false`  | Allow `delete_source` to remove Dropbox files.                            |

*Exactly one credential path is required: a static `access_token`, or a
`refresh_token` + `app_key` (+ optional `app_secret`) grant. Since Dropbox
access tokens only live ~4 hours, the refresh grant is the durable option —
the adapter exchanges it via `oauth2/token`, caches the minted access token
process-wide for its declared lifetime, and retries once with a fresh token
when a cached one is rejected.

The Dropbox app itself must be a "scoped access" app with
`account_info.read`, `files.metadata.read` and `files.content.read`
permissions (plus `files.metadata.write` and `files.content.write` when tag
write-back, rename or delete is wanted). Scope grants are not retroactive:
enabling a permission on the app does not extend tokens that were already
issued — after changing app permissions the account must be re-connected so
a new token is granted with the updated scope set.

Streaming defaults to proxying the file body through `files/download`, which
honours `Range` headers — it works in every browser. Setting
`temporary_links` redirects clients to short-lived `get_temporary_link` URLs
instead (`safe_to_redirect`; the client fetches bytes directly from Dropbox,
including HTTP ranges; links are valid ~4 hours). That offloads bandwidth to
Dropbox's CDN but **fails in Firefox**: `dl.dropboxusercontent.com` serves
`Content-Security-Policy: sandbox`, which Firefox applies to media loads —
enable it only for Chromium/Safari audiences. Tag write-back re-downloads the
file, rewrites tags locally, and re-uploads it in place — payloads above
Dropbox's 150 MiB single-upload cap go through chunked upload sessions
automatically.

`iter_items` pages `files/list_folder` (metadata only) and reports each
file's `rev` as the change token via `detect_changes`, so unchanged trees
cost a listing and no file transfer. The provider `content_hash` (SHA-256 of
4 MiB block hashes) is kept on `ExternalItemRef.checksum` but is never
treated as the audio sha256. As with the other remote providers, freshness
comes from scheduled syncs rather than filesystem watching.

#### OAuth connect flow

Providers that authenticate through OAuth (`dropbox` and `gdrive` today;
Spotify, Tidal and YouTube are planned) offer a "Connect" button in the
external-library form instead of making users paste tokens by hand. The
machinery is provider-agnostic (`external/oauth.py`): each provider
registers an `OAuthProviderSpec` describing its authorize/token endpoints,
which config keys carry the OAuth client id/secret, and how token-response
fields map onto adapter config keys. A spec may also set
`scopes_for_config` to derive the requested OAuth scopes from the submitted
config — Google Drive uses it to request `drive.readonly` or the full
`drive` scope depending on the form's write flags. Provider listings report
`oauth_supported` and `oauth_callback_url` (the public
`{base}/api/v1/external-libraries/oauth/callback` URL built from
`public_base_url`) so the frontend only shows the button where it applies
and can tell the user which redirect URI to register in the provider's app
console.

The flow runs in three steps:

1. `POST /api/v1/external-libraries/oauth/begin` (authenticated) validates
   the provider type and stores a pending entry in Redis — bound to the
   initiating user, the client credentials, a PKCE verifier, the callback
   `redirect_uri` and the page to return to — under a random `state` key
   (10-minute TTL). The response carries the provider's authorization URL,
   which the SPA navigates to after stashing the form in `sessionStorage`.
2. `GET /api/v1/external-libraries/oauth/callback` (unauthenticated; the
   `state` parameter binds it to the pending flow) consumes the pending
   entry once, exchanges the authorization code for tokens server-side and
   stores the resulting config fragment under a result key (5-minute TTL)
   before redirecting back to the SPA. Provider errors and exchange
   failures redirect with `oauth_error` instead; `return_to` is restricted
   to local paths.
3. `POST /api/v1/external-libraries/oauth/claim` (authenticated) lets the
   SPA collect the fragment once — results are deleted on claim and a claim
   by a different user leaves the entry in place.

The granted fragment is merged into the provider configuration and only
persisted when the user saves the form, at which point it goes through the
same validation and Fernet encryption as manually entered credentials.
Dropbox uses `token_access_type=offline` and Google Drive
`access_type=offline` with `prompt=consent` so the granted `refresh_token`
is durable; a future provider only needs to register a spec — no new
routes, Redis plumbing or frontend redirect handling.

#### Google Drive provider

The `gdrive` provider (`external/_gdrive.py`, httpx) indexes audio files
stored in a Google Drive folder, including shared (team) drives. **The
Songhive instance must be able to reach `googleapis.com`** — every listing,
download, and mutation originates server-side through the Drive API v3.
Like the other providers, both regular users (when
`allow_user_created_libraries` permits the provider) and admins can attach
Drive roots.

A Google Drive external library stores the following adapter config:

| Key                        | Required | Default  | Description                                                             |
|----------------------------|----------|----------|-------------------------------------------------------------------------|
| `client_id`                | OAuth    | —        | OAuth 2.0 client ID of a Google Cloud "Web application" client; the     |
|                            |          |          | Connect flow fills `access_token`/`refresh_token` from it.              |
| `client_secret`            | OAuth    | —        | OAuth 2.0 client secret (stored encrypted).                             |
| `refresh_token`            | OAuth    | —        | OAuth refresh token; granted by the Connect flow (stored encrypted).    |
| `access_token`             | yes*     | —        | Static bearer token; expires after ~1h — testing only.                  |
| `service_account_key`      | yes*     | —        | Service account JSON (`client_email`/`private_key`); takes precedence.  |
| `root_folder_id`           | no       | `root`   | Folder ID to index; defaults to the My Drive root, or the shared drive  |
|                            |          |          | root when `drive_id` is set.                                            |
| `drive_id`                 | no       | —        | Shared drive ID; enables `supportsAllDrives` and scopes lists to it.    |
| `token_uri`                | no       | Google   | OAuth token endpoint override.                                          |
| `verify_ssl`               | no       | `true`   | Verify TLS; set to `false` to disable or to a CA bundle path.           |
| `timeout`                  | no       | `30`     | HTTP request timeout in seconds.                                        |
| `extensions`               | no       | all audio| List of file extensions to index.                                       |
| `exclude`                  | no       | `[]`     | `fnmatch` patterns applied to display paths and file names.             |
| `recursive`                | no       | `true`   | Whether to scan subfolders.                                             |
| `allow_hashing`            | no       | `true`   | Whether to compute audio hashes for new/updated files.                  |
| `fast_hash`                | no       | `false`  | Hash raw file bytes instead of ffmpeg audio-only hashing.               |
| `trash_on_delete`          | no       | `true`   | Move files to the Drive trash instead of permanently deleting.          |
| `allow_write_tags`         | no       | `false`  | Rewrite embedded tags by re-uploading the file in place.                |
| `allow_rename_source`      | no       | `false`  | Allow `rename_source` to rename remote files (file ID stays stable).    |
| `allow_delete_source`      | no       | `false`  | Allow `delete_source` to trash or remove remote files.                  |

*Exactly one credential mode is required: OAuth user credentials, a service
account key, or a static access token. The UI "Connect" button runs the
generic OAuth connect flow against `accounts.google.com` — the user
registers a "Web application" OAuth client in the Google Cloud console with
the reported `oauth_callback_url` as an authorized redirect URI, and the
granted `refresh_token` (requested with `access_type=offline` +
`prompt=consent`, so re-connects always return one) lands in the config
automatically. The authorize request asks for `drive.readonly`, or the full
`drive` scope when any write flag is enabled in the form. Service account
access tokens are minted locally with an RS256 JWT bearer grant (using the
`cryptography` package already required for secret-at-rest encryption) and
request the same scope rule. OAuth tokens are refreshed through the token
endpoint and cached for their stated lifetime.

Unlike path-addressed providers, Google Drive items are identified by their
file ID, so `provider_key` carries the ID (stable across renames and moves)
and `display_path` carries the folder path reported by the last listing.
`iter_items` walks the folder tree with `files.list` (metadata only) and
reports the `md5Checksum` — a content-addressed change token — via
`detect_changes`, so unchanged trees cost a listing walk and no file
transfer. Google-native documents (`application/vnd.google-apps.*`) have no
downloadable payload and are skipped. Drive has no presignable URL, so
`open_stream` always returns a proxied byte iterator with HTTP `Range`
support; freshness comes from scheduled syncs rather than filesystem
watching, as with the other remote providers.

#### Visibility, sharing, and secret redaction

Every external library is backed by a normal `Library` row, so visibility
(`private`/`local`/`public`) and `ShareGrant` sharing behave exactly like
regular libraries. `PATCH /api/v1/external-libraries/{id}` (and the admin
equivalent) accepts a `visibility` field; changing it updates the backing
library and calls `services.music.propagate_external_library_visibility`,
which rewrites the visibility of all linked synced tracks so list queries
and single-track ACLs stay consistent. `PATCH /api/v1/libraries/{id}` on an
external-backed library propagates the same way. Non-admin propagation only
touches tracks the caller owns, mirroring album visibility propagation.

Because API responses redact secret-bearing config keys to `"<redacted>"`,
both PATCH routes run submitted configs through
`_merge_config_preserving_redacted`: any key still carrying the sentinel is
restored from the stored (decrypted) config before validation and
re-encryption, so editing non-secret fields never clobbers credentials.

The web UI renders a per-provider configuration form instead of raw JSON.
Provider form templates live in
`frontend/src/config/externalLibraryProviderTemplates.ts`. Each template entry
lists the JSON property name, i18n label/description keys, field type (string,
password, number, boolean, enum, comma-separated string array, or multiline
textarea), and default value. A field may also declare a `configKey` to
read/write a different JSON property than its own name — used for union-typed
keys such as WebDAV's `verify_ssl`, where the optional CA-bundle text field
shares the key with the verification toggle and overrides it when non-empty.
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
- `services/streaming.collect_external_stream` materializes iterator/url
  streams into memory or a temp file. Audio-typed payloads in MP4 containers
  get their `ftyp` major brand rewritten to `M4A ` — audio-only MP4s often
  carry a generic `isom`/`mp4*` brand that content sniffers (libmagic, and
  therefore Fediverse media fetchers such as Mastodon) report as
  `video/mp4` regardless of the served `Content-Type`, which remote servers
  then process (and reject) as video. Non-temporary `path` streams are never
  rewritten — they reference files owned by the adapter (e.g. the local
  library). The same caveat applies to local `StoredFile` downloads, which
  are served straight from storage and not normalized.

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

## API Adapters (Subsonic)

Besides the native `/api/v1` surface, Songhive can expose *foreign* media APIs
so third-party clients can browse and stream its content. These integrations
live in `songhive/adapters/` — a different adapter family from
`songhive/external/` (which *imports* remote libraries into Songhive). API
adapters map Songhive's services and ACLs onto an external protocol; the
first implementation is Subsonic/OpenSubsonic, and the registry is designed
for future Icecast/Mopidy/Jellyfin adapters.

Framework (`songhive/adapters/`):

- `base.py` — `APIAdapter` interface: `name`, `is_enabled(config)`,
  `router()` (FastAPI routes), `tornado_routes()` (native handlers for
  streaming), `include(app, config)`.
- `registry.py` — name-keyed registry (`register_adapter`, `get_adapter`,
  `list_adapters`), `mount_adapters(app, config)` invoked from `create_app`,
  and `adapter_tornado_routes(config)` consumed by `_build_tornado_app`
  ahead of the WSGI fallback.
- Each adapter owns its complete URL namespace (Subsonic owns `/rest`).

Subsonic adapter (`songhive/adapters/subsonic/`):

- **Namespace** — `/rest/*.view`, the path Subsonic clients hard-code.
  `ping`, `getLicense`, `getMusicFolders`, browsing (`getIndexes`,
  `getArtists`, `getArtist`, `getAlbum`, `getSong`, `getMusicDirectory`,
  `getArtistInfo*`, `getAlbumInfo*`), `getAlbumList*`, `getGenres`,
  `getSongsByGenre`, `getRandomSongs`, `search*` variants, playlists
  (`getPlaylists`, `getPlaylist`, `createPlaylist`, `updatePlaylist`,
  `deletePlaylist`), starring (`getStarred*`, `star`, `unstar`),
  `scrobble`, `nowPlaying`, `getNowPlaying`, `stream`, `download`,
  `getCoverArt`, `getAvatar`, plus an OpenSubsonic
  `getOpenSubsonicExtensions` advertisement and a `/rest/{method}.view`
  catch-all that returns a protocol error for unimplemented methods.
- **Envelope** — every endpoint answers with the `subsonic-response`
  envelope (protocol `1.16.1`, `type=songhive`, `openSubsonic=true`) in XML
  by default, JSON via `f=json`, or JSONP via `f=jsonp`+`callback`. JSONP
  callbacks are restricted to safe identifier paths to prevent script
  injection.
- **Authentication** — per-request `u`+`p` (plain, `enc:`-hex, or a Songhive
  API token in `p`), `u`+`apiKey` (OpenSubsonic), and `u`+`t`/`s`
  salted-token auth. Salted tokens (`md5(password + salt)`) can only be
  verified against credentials the server can reconstruct, so the real
  password is `p`-only — but API tokens work: HS256 JWTs are deterministic
  given their claims, and every claim is recoverable from the `api_tokens`
  row (`user_id`, `jti`, `expires_at`, plus `created_at` which
  `issue_api_token` pins to the `iat` claim). `auth.py` rebuilds each
  candidate JWT byte-for-byte (trying `created_at` ±1s for tokens issued
  before the pin) and compares `md5(jwt + s)` to `t`, so salted-token
  clients work with an API token as the password while no usable
  credential is stored. Because credentials ride in request parameters
  rather than cookies, `/rest/` is exempt from the CSRF middleware.
- **ACL mapping** — every browse/stream path goes through the same
  `services.acl` checks as `/api/v1`: anonymous users see public content,
  authenticated users see public/local/owned/shared content, inaccessible
  items map to error 50 (or 70 where the spec calls for not-found).
- **Streaming** — under the Tornado bootstrap `/rest/stream.view` and
  `/rest/download.view` are served natively by
  `handlers.py` (subclasses of `StreamHandler`, reusing its range,
  transcoding and external-stream logic with Subsonic auth/error mapping).
  `media.py` registers the same endpoints on the FastAPI router as the
  uvicorn fallback; `getCoverArt`/`getAvatar` are FastAPI-only.
- **Playback reporting** — `scrobble` records listens via
  `services.streaming.record_listen`; `nowPlaying`/`getNowPlaying` share an
  in-process `now_playing.py` registry (also populated when tracks are
  streamed). Mutating playlist endpoints write `AuditLog` rows through
  `services.audit.log_action`.
- **Config** — `subsonic.enabled` (default `true`) gates mounting; nginx
  proxies `/rest/` with `proxy_buffering off` for streaming.

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
   expiry, forming a user session. The JSON response still carries the token
   pair, but the server also sets three cookies (`api/cookies.py`):
   `access_token` and `refresh_token` are `HttpOnly` so browser JavaScript
   never reads the credentials, while a readable `csrf_token` backs the
   double-submit CSRF check. The refresh cookie is scoped to
   `Path=/api/v1/auth`; cookie flags are controlled by `auth.cookie_secure`
   (defaults to secure outside debug mode), `auth.cookie_samesite` (`lax`),
   and `auth.cookie_domain`.
2. **Refresh** — opaque refresh token from the JSON body (API clients) or the
   `refresh_token` cookie (browsers) → Redis lookup → rotate (revoke old,
   issue new pair) and re-set the cookies. Failed refreshes clear the cookies.
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

- **JWT middleware** — `api/middleware/auth.py` decodes the bearer token
  (`Authorization: Bearer …` first, then the `access_token` cookie) and
  injects the current `User` via FastAPI's dependency system. Cookie auth is
  what lets `<img>`/`<audio>` elements, file downloads, and the WebSocket
  handshake authenticate without JavaScript-accessible tokens.
- **CSRF** — `api/middleware/csrf.py` enforces a double-submit check: unsafe
  methods (POST/PUT/PATCH/DELETE) that carry an auth cookie and no
  `Authorization` header must echo the readable `csrf_token` cookie in
  `X-CSRF-Token`. Session endpoints (login/refresh/logout/registration, OAuth
  token endpoints) are exempt; bearer clients and safe methods are unaffected.
- **Role-based** — `UserRole.ADMIN` / `MODERATOR` / `USER`; `require_admin`
  dependency enforces admin-only routes.
- **ACL service** (`services/acl.py`) — three-level visibility check augmented
  with `ShareGrant` (owner grants a named user) and `ShareToken` (revocable
  short-link cookie). `Artist` rows carry no `visibility`/`owner_id` of their
  own, so artist listings (`GET /api/v1/artists/` and the artists section of
  `GET /api/v1/search/`) instead require at least one track or album the
  requester can access; artists whose content is entirely private or unshared
  are hidden. Admins bypass the filter. `GET /api/v1/artists/` also accepts
  `owner_username` (like the album/track/library/playlist listings) to keep
  only artists with at least one track or album owned by that user, on top of
  the requester's ACL. `StoredFile` rows inherit access from
  the entities referencing them: a file attached to a track, album, playlist,
  or library is downloadable by anyone who can access that entity (directly,
  via a `ShareGrant`, or via a `ShareToken`). Album covers are also reachable
  through the album's tracks, and artist images through the artist's tracks
  and albums, so sharing a track reveals its album cover and artist image.
- **Rate limiting** — Redis sliding-window; `rate_limit` (IP), `rate_limit_user_or_ip`
  (authenticated users keyed by id), and `rate_limit_account` (always per-user)
  FastAPI dependencies. Media `DELETE` endpoints use `rate_limit_account` for
  per-user rate limiting on destructive operations. Fails open when Redis is unavailable.
- **CORS** — the API uses the credentialed `CORSMiddleware` allowlist from
  `server.cors_origins`. Read-only media endpoints
  (`GET /api/v1/files/{id}/download`, `GET /api/v1/tracks/{id}/download`, and
  `GET /api/v1/stream/{id}`) instead return `Access-Control-Allow-Origin: *`
  via `api/middleware/media_cors.py` (and `set_default_headers` in the Tornado
  `StreamHandler`) so remote Fediverse clients can embed audio directly — this
  is safe because the wildcard can never be combined with credentials, and the
  `access_token` cookie is `SameSite=Lax` so it is not sent cross-origin.
  Preflights on those paths allow the `Range` header and expose
  `Content-Range`/`Accept-Ranges`/`Content-Length` to fetch-based players.

### Owner exposure

Entity detail responses for `Track`, `Album`, `Library`, and `Playlist` include `owner_id` whenever the requester has ACL access to the entity; `?include=owner` adds a nested `UserSummary` with `actor_url`, `username`, `display_name`, and `avatar_url`. The frontend `useEntityMeta` composable returns this full owner object, and `UserLink` routes local users to `/@{username}` and remote users to their `actor_url`.

### Profile visibility

Each user carries a `profile_visibility` preference (`public`, `local`, or
`private`; default `public`, editable from `/settings` via
`PATCH /api/v1/users/me`) controlling whether their profile appears in the
users directory. `GET /api/v1/users` and the users section of
`GET /api/v1/search/` hide `local` profiles from anonymous callers and never
list `private` ones — not even to their owner. Individual profile pages
(`GET /api/v1/users/{username}`, `/@{username}`) remain reachable regardless
of the setting.

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

### Server-side audio outputs

Playback can be routed to persistent server-side outputs instead of the
browser Web Player, Spotify-Connect-style: the browser (or any client) sends
commands, but audio is rendered by the server, so playback continues after
the controlling tab closes.

- **Control plane** — a persisted `PlaybackSession` row
  (`models/playback_session.py`) holds the queue, index, position, volume and
  play/pause state per user. `services/playback.py` validates commands,
  commits the new state, then publishes a control envelope on the Redis list
  `songhive:playback:control:{session_id}`. Session changes are broadcast
  over `/ws/events` (`playback_session` events) so multiple tabs stay in
  sync.
- **Outputs** — an `OutputStream` row (`models/output_stream.py`) stores a
  `provider_type` plus a provider config. Secret config fields are
  Fernet-encrypted at rest and redacted to `"<redacted>"` in API responses;
  PATCH routes merge the sentinel back to the stored value. Providers are
  registered in `streams/registry.py` by subclassing `AudioOutput`
  (`provider_type`, `FIELDS` schema hint, `validate_config`,
  `create_driver`); `GET /api/v1/outputs/providers` exposes the schema to
  the frontend form builder.
- **Stream worker** — `songhive stream-worker` (`streams/worker.py`) is a
  dedicated process that scans for active sessions bound to outputs and runs
  one `SessionDriver` per session. Each driver holds a Redis lock
  `songhive:stream:lock:{output_id}` (unique token, refreshed every loop,
  released on shutdown) so exactly one worker drives a given output. The
  worker owns queue advancement, repeat/shuffle, listen recording,
  scrobbling and idle shutdown (`streams.background_idle_timeout_seconds`).
  Deployed as the `stream-worker` Compose service (`streams` profile) and
  `songhive-stream-worker.service`.
- **Icecast provider** (`streams/icecast.py`) — one long-lived ffmpeg
  *encoder* pushes an MP3, Ogg Vorbis or Opus stream to an `icecast://`
  mountpoint;
  a short-lived ffmpeg *decoder* (`-re -ss <pos>`) feeds PCM into it per
  track. Pausing swaps the decoder for a **realtime** (`-re`) `anullsrc`
  silence generator so the mount stays alive without flooding listeners.
  `source_ended` decoder events carry a generation tag so events from a
  killed decoder can't pause the new source. ffmpeg's Icecast muxer does not
  support mid-stream ICY metadata, so `update_metadata` is a no-op.
- **Native HTTP provider** (`streams/http.py`) — same driver machinery, but
  the encoder writes to `pipe:1` and a publish task XADDs each ~16 KiB chunk
  (base64) into the Redis stream `songhive:stream:data:{mount}`
  (`MAXLEN ≈ streams.http_stream_max_entries`). The Tornado
  `StreamMountHandler` (`streaming/mount.py`) serves `GET /streams/{mount}`
  by bursting the newest `streams.http_stream_burst_entries` entries
  (`XREVRANGE`) then following the stream (`XREAD BLOCK`) — every listener
  is an independent cursor, so Redis provides the fan-out and a stalled
  client never slows the others. Entries older than
  `streams.http_stream_max_lag_seconds` (entry IDs are server millisecond
  timestamps) are skipped rather than delivered, so a listener that falls
  behind jumps forward instead of accumulating latency, and the
  `X-Accel-Buffering: no` response header keeps buffering proxies from
  hiding listener lag in their own buffers. Liveness is the TTL'd
  `songhive:stream:meta:{mount}` key refreshed by the driver; `{"end": "1"}`
  entries disconnect listeners on graceful stop; per-listener TTL keys under
  `songhive:stream:listener:{mount}:*` feed `listener_count` for idle
  shutdown and enforce `streams.http_stream_max_listeners` (0 = uncapped).
  Mount slugs must be unique across `http` outputs; an optional
  `listen_token` field gates listeners via `?token=`/`Bearer`. No external
  server or extra port is needed.
- **Snapcast provider** (`streams/snapcast.py`) — same driver machinery, but
  the "encoder" is an ffmpeg *passthrough*: raw s16le stereo PCM from stdin is
  copied unchanged to a snapserver sink — either the named pipe of a
  snapserver `pipe://` source (`mode=fifo`, auto-created with `mkfifo`,
  existing non-FIFO paths rejected so a typo can't clobber a regular file) or
  a snapserver `tcp://` listening source (`mode=tcp`), which also allows a
  remote snapserver. Snapcast streams are never registered dynamically: the
  source must already be declared in `snapserver.conf` (e.g.
  `stream = tcp://0.0.0.0:4953?name=...&sampleformat=...`), the target `port`
  is that source's listener — *not* the snapclient port 1704 — and
  `sample_rate` must match the source's `sampleformat`. `listener_count`
  queries snapserver's JSON-RPC control API (`Server.GetStatus` over raw TCP,
  default port 1705 — the HTTP/snapweb port 1780 does not speak the
  newline-delimited protocol; `control_host`/`control_port` fields) and
  counts connected, unmuted clients — optionally only those in groups playing
  `stream_name` — so idle shutdown tracks real listeners like the Icecast
  status endpoint does.
- **Driver interface** (`streams/driver.py`) — `start`, `stop`,
  `set_source`, `pause`, `resume`, `seek`, `set_volume`, `update_metadata`,
  `health`, `is_paused`, `listener_count`, and an `events` queue. The worker
  uses `driver.is_paused` (not the persisted session state, which is already
  post-command by the time the worker reads it) to detect pause→play
  transitions.

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
  instance's `/tags/{name}` route convention is injected via
  `federation/_common.py`'s `get_tag_url`.
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

- Uploaded tracks are published as `Audio` objects via `Create` activity —
  only when explicitly requested. Uploads are local-only by default; the
  `/api/v1/files/upload` endpoints (single and bulk) and the
  `/{library_id}/tracks` upload endpoints take an opt-in `publish=true`
  flag (surfaced in the UI as a "Publish on the Fediverse" checkbox shown
  when the instance federates), which the synchronous library bulk path and
  the background `process_upload` Celery task honour. Without the flag the
  track receives no `federation_object_id` and no publication activity.
  `process_upload` defaults the flag to `true` so directory scans keep
  publishing imported public tracks.
- The published `Audio` object carries the track's `description` (a free-text
  field settable at upload time or via `PATCH /tracks/{id}`) as its
  `content`: the text is HTML-escaped, http(s) URLs become anchors with
  scheme-less link text, and `#tags` become `rel="tag"` links to this
  instance's `/tags/{name}` pages. Tags found in the description are
  also appended to the object's `tag` list, and the media download URL is
  attached as a `Document` with the audio MIME type. The rendered `content`
  ends with a `<p><a href="{track_url}">{artist} - {title}</a></p>` link
  back to the track page (`normalize_post_content`), and `summary` is left
  unset: Akkoma and Mastodon both treat a non-empty `summary` as a content
  warning, so mirroring the post body into it surfaced the raw HTML as a CW
  header on Akkoma. The object's `name` is plain text
  `{artist} - {title}` (HTML-escaped): Akkoma already wraps `name` in its
  own anchor to the object `url`, so an HTML `name` produced nested
  anchors. The object also carries a stable `published` timestamp (the
  track's `created_at`) — Akkoma falls back to the Unix epoch when it is
  missing — and the `Create` envelope is stamped with a unique `id` and its
  own `published`. The `url` links
  also mirror `mediaType`
  into `mimeType` — Mastodon's link selection reads the non-standard
  `mimeType` key and defaults untyped links to `text/html`, so without it
  the raw audio download URL would be chosen for display instead of the
  track page.
- Each public lifecycle gets a fresh `Track.federation_object_id` (generated
  on every transition to public) so a previous `Tombstone` at the same URL
  cannot block re-publication.
- `POST /api/v1/tracks/{id}/publish` publishes a public track to the
  fediverse at any time (the "Fediverse" tab of the share dialog). Any
  authenticated user may share a public track as a `Create(Note)` post
  under their own actor; republishing the canonical `Create(Audio)`
  object (`object_type=audio`, which re-mints
  `Track.federation_object_id`) is restricted to the track's owner and
  admins. The publication activity is attributed to the publisher — its
  `owner_user_id` — who can edit or retract it via the activity
  endpoints even when they cannot manage the track itself. It accepts an
  optional `status` — a one-off post text used
  as the object's `content` instead of the stored
  `description`, run through the `process_mentions` pipeline so `@handle`s
  become links, `Mention` tags, and `activity_mentions` rows — an
  optional `visibility` (default `public`) selecting the post's audience:
  `public`/`followers`/`mentioned` federate to their respective audiences,
  while `private`/`local` record the activity without delivering it, and an
  optional `object_type` (default `note`). `note` shares the track as a
  `Create(Note)` — the post body renders on every remote server (Mastodon
  drops `content` on `Audio` objects, which it treats as a converted type)
  — with the stream embedded as an `Audio`-typed attachment linked to the
  track's canonical object when it has one; each share mints its own object
  id, and the share's `url` is that object id rather than the track page:
  the track URL dereferences to the canonical `Audio` object, so a remote
  URL lookup (e.g. Mastodon's search box) resolves the `Audio`, while the
  share keeps a distinct identity resolvable through its own object URL.
  The track page still ends the share's `content` as the appended
  `{artist} - {title}` link — `normalize_post_content` takes the page URL
  explicitly (`link_href`) since it can no longer be read from the object's
  `url`. `audio` republishes the canonical `Create(Audio)` media object.
  The status is never persisted on the track. The `to`/`cc` addressing derived
  from the visibility is applied to both the `Create` envelope and the
  embedded object. Each `audio` publication mints a fresh
  `federation_object_id` so every post is a distinct remote object.
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
  `Audio` object, with spaces converted to underscores. These tags include
  an `href` pointing at the instance's `/tags/{name}` page.
- Per-actor follower isolation is delegated to pubby's `target_actor_id`.
- Incoming `Follow`/`Undo(Follow)` activities persist or remove rows in
  pubby's `federation_followers` storage; pubby's `InboxProcessor` also
  retracts the follower when a remote actor's `Delete` targets the
  sending actor itself — scoped to the actor the processor is bound to.
  For shared-inbox deliveries that binding is the instance actor, so
  `process_incoming` extends the retraction: a verified self-`Delete`
  removes all of the actor's follow records
  (`_retract_shared_inbox_follows`). The sweep only runs after pubby's
  signature verification bound the signer to `activity.actor`, and is
  skipped for blocked/non-allowed domains, which the processor drops
  before verifying. `services/federation.get_actor_followers` returns
  an actor's followers newest-first by `followed_at` and
  `count_followers_by_actor` provides per-actor counts, backing the
  `followers_count` field on `GET /api/v1/users/{username}` and the
  paginated `GET /api/v1/users/{username}/followers` JSON endpoint
  (distinct from the ActivityPub `OrderedCollection` served at
  `/users/{username}/followers`). The SPA shows the count on
  `/@{username}` and renders follower details at `/@{username}/followers`.
- *Follower approval*: each user's `followers_approval` preference
  (`accept`, `manual`, or `reject`; default `accept`, editable from
  `/settings` via `PATCH /api/v1/users/me`) decides how incoming `Follow`
  activities are handled. `process_incoming` passes pubby's
  `InboxProcessor` a `follow_policy` callback that resolves the target
  actor URL to the owning user's setting (object follows and the
  instance actor keep the auto-accept default). `accept` stores the
  follower and replies `Accept` as before; `manual` stores a pending
  `FollowRequest` in pubby's `federation_follow_requests` storage, sends
  no reply, and flags the resulting `follow` notification with
  `follow_request_pending` so the UI can render accept/reject controls
  in the notification body; `reject` answers with a `Reject` activity
  and stores nothing (no notification either). Pending requests are
  owner-only: `GET /api/v1/users/me/follow-requests` lists them and
  `POST .../follow-requests/accept|reject` resolve them through
  pubby's `accept_follow_request`/`reject_follow_request`, which embed
  the original `Follow` in the response, promote approved requests to
  `federation_followers` rows, and hand the reply to the
  `deliver_activity` Celery task for signed delivery. Resolving a
  request also rewrites the notification payload to
  `follow_request_status` (`accepted`/`rejected`) and pushes a
  `notification_updated` event. `Undo(Follow)` removes a still-pending
  request. The SPA exposes requests as an owner-only "Requests" tab on
  `/@{username}/followers`; the actor document advertises
  `manuallyApprovesFollowers` (and the Mastodon-compatible account sets
  `locked`) while the policy is `manual`.
- *Object follows*: a `Follow` may also target a local **object** rather
  than an actor — Friendica sends `Follow` on a thread's root item
  (`parent-uri`) for conversation subscriptions, the FEP-efda
  "followable objects" pattern. Pubby stores those rows scoped to the
  object's own URL in `target_actor_id`, so they never count as actor
  followers (the counts above key on the actor URL) and `Undo(Follow)`
  retracts them by the same key. `Follow`s targeting *remote* actors or
  objects are dropped by the processor without an `Accept` — the remote
  server owns their followers collection. Object-scoped rows are read in
  bulk through `storage.get_followers_of_targets(...)` and their inboxes
  collected by `services/federation.get_object_follower_inboxes`:
  `resolve_audience` fans public activities out to followers of the
  activity's own object id and every `in_reply_to_activity_id` ancestor
  (covering replies to followed threads), and newly materialized public
  remote replies/quotes are relayed to the same subscribers — the raw
  activity forwarded verbatim and signed by the nearest local ancestor's
  owner, or the instance actor when the chain has no local node
  (`federation/incoming._relay_thread_activity`). Served object documents
  advertise their `followers` collection (an `OrderedCollection` of the
  subscribers' actor ids) at
  `GET /users/{username}/objects/{object_id}/followers`. An object-scoped
  `Follow` creates a `follow` notification only for the object's owner —
  a personal-inbox delivery addressed to another user is misaddressed and
  skipped — with `target_*` payload fields describing the followed
  object, matching the reply/quote payload convention.

**Outbound follows** — a local user following a local or remote actor —
are owned by `services/follows.py` and persisted in the `follows` table
(`models/follow.py`), which is the mirroring *outbound* side of pubby's
inbound follower/request tables. `POST /api/v1/users/me/follows` accepts a
bare local username, a `@user@domain` handle, or an actor/profile URL
(`remote_content.parse_remote_target` splits local from remote inputs,
`lookup_remote_actor` resolves remote actors through the guarded fetch
and actor cache). Local targets apply the owner's `followers_approval`
policy immediately — `accept` writes the pubby follower row and stores an
`accepted` follow, `manual` writes a pending `FollowRequest` and keeps
the row `pending`, `reject` fails with 403 — and the target gets the
usual `follow` notification. Remote targets store a `pending` row whose
`activity_id` matches the `Follow` activity's own id, enqueue it signed
on `deliver_activity`, and stay pending until the remote server answers:
`process_incoming` folds inbound `Accept`/`Reject` activities back into
the row via `apply_follow_decision` (matched on the deciding actor being
the follow target plus the wrapped activity id or follower actor URL;
`Accept` marks the row `accepted`, `Reject` drops it). `DELETE
/api/v1/users/me/follows` reverses the relationship: local targets drop
the pubby follower/request rows and retract the notification, remote
targets get a signed `Undo(Follow)` embedding the original activity
(rebuilt from the stored `activity_id`) delivered to the recorded inbox.
When a local owner resolves a pending request, the accept/reject
endpoint also updates the local requester's row directly through
`apply_local_follow_decision` — the federated reply only reaches remote
requesters. `GET /api/v1/users/me/follows` lists the caller's follows in
all states; `GET /api/v1/users/{username}/follows` lists `accepted`
follows publicly and includes `pending` rows for the owner. Profiles
expose `follows_count` plus the viewer-relative `follow_state`, and the
remote-actor endpoint reports `follow_state` for authenticated callers.
The SPA renders the list at `/@{username}/follows` (pending badges for
the owner) and follow/unfollow buttons on local and remote profiles.

**Inbound remote replies, quotes and announces** are materialized into
`Activity` rows by `federation/incoming.py`, invoked from
`tasks/federation.py`'s `process_incoming` for
`Create`/`Update`/`Delete`/`Accept`/`Reject`/`Announce`/`Undo`
activities after pubby's `InboxProcessor` has run. Admission is gated on
the outbound-follow graph: a `Create` is only stored when its
`activity.actor` is followed by at least one local user
(`follows_service.actor_is_followed`) — objects fetched explicitly
through remote URL lookup arrive through the dereference path instead,
so nothing from unfollowed actors accumulates locally. `Update`s to
already-stored objects always apply (the row was admitted by the Create
gate or explicit lookup); an `Update` for an unknown object — replaying
a missed `Create` — is subject to the same followed-actor rule, and
`Accept`/`Reject` activities additionally answer outbound `Follow`s and
`QuoteRequest`s. A `Create` whose object replies
to — or quotes — a known activity (matched by `source_id` or the
`/objects/{id}` permalink form) becomes a `source_type="remote"`
`reply`/`quote` row attached to the parent's entity, linked through
`in_reply_to_activity_id`, carrying the raw activity as `payload` and the
remote actor as `source_actor`. Quote fields (FEP-0449 `quote`, Mastodon
`quoteUrl`, Misskey `_misskey_quote`) take precedence over `inReplyTo`,
mirroring pubby's interaction typing. Publicly
addressed objects keep the entity-clamped `public` visibility; non-public
ones (direct messages, followers-only) are stored with `mentioned`
visibility when they address at least one local user — through
`to`/`cc`/`bto`/`bcc` addressees or `Mention` tags — each resolved local
addressee getting an `ActivityMention` row so only that audience sees the
post in the listing. Non-public objects addressing no local user are not
materialized. A `Create` whose object targets no known activity — a
standalone post, or a reply/quote whose parent was never cached — is
instead stored through `remote_content.materialize_remote_post`: the
object is upserted into `remote_objects` (same shape as explicit URL
lookup, minus the fetch) and mirrored as an `entity_type="remote"`
`Activity`, so a followed actor's outbox shows up on their remote
profile's posts tab. Non-public standalone posts keep `private` cache
visibility and `local` activity visibility. `Update` revises
content, payload, mentions, hashtags and visibility (materializing
posts whose `Create` was missed, degrading rows that lose their
public audience to `mentioned`, and refreshing the mirrored
`remote_objects` row for standalone posts); `Delete` soft-deletes the
row and tombstones the cache row's `unavailable_at`, but only for the
recorded `source_actor`. Attribution is enforced by pubby's `attribution.validate`:
`process_incoming` enables `strict_attribution` on the `InboxProcessor`,
and materialization/update re-apply `pubby.validate_attribution` to the
raw activity since the sync runs regardless of the processor's verdict —
the delivering actor must match `attributedTo` and share the object's
host. An `Accept` answering a `QuoteRequest`
we sent (`apply_quote_authorization`) stamps the issued
`QuoteAuthorization` id onto the local `quote` row's stored `Create`
payload — and fans out an `Update` — but only when the accepting actor is
the quoted post's author and the `instrument`/`object` match the recorded
quote and target. Because the Pubby `federation_interactions` record
is kept alongside the row, reply/quote counts and listings deduplicate on
`object_id`; boosts (stored `announce` rows vs. recorded `BOOST`
interactions) deduplicate on `activity_id` — the announce's own id —
since the interaction record has no separate object id. Materialized
replies and quotes render as regular activity
cards and
accept the same interactions as local ones: likes and boosts federate
to the remote author's inbox through `fan_out_like_activity`/
`fan_out_boost_activity`, and local replies/quotes address the remote
author via
a `Mention` tag with `inReplyTo`/quote fields set to the remote
`source_id`.

An inbound `Announce` from a followed actor
(`materialize_remote_announce`) is stored as a `source_type="remote"`
`announce` row attached to the boosted activity's entity through
`in_reply_to_activity_id` — the same shape `boost_activity` produces
locally — so the boost surfaces in timelines as an "X boosted" card. A
boosted object unknown locally is first fetched through
`remote_content.dereference_remote_object` — the guarded remote fetch
that upserts the `remote_objects` cache row, materializes the object's
mirror `Activity`, and caches its author actor — and the boosting actor
is refreshed through `lookup_remote_actor` so the card renders a
profile. Publicly addressed announces inherit the target's visibility;
non-public ones are stored with `mentioned` visibility only when they
address a local user. `Undo(Announce)` — whether it wraps the full
announce or only its id — soft-deletes the row, and `Delete` of an
announce retracts it without tombstoning the boosted object's cache row
(only rows mirroring the deleted object itself tombstone their
`remote_objects` row). Likes stay interaction-only.

**Instance-level actor:**

- `_setup_federation()` in `api/app.py` configures an `Application`-type
  actor via pubby's `ActorConfig` and mounts both ActivityPub and Mastodon API
  compatibility endpoints.

**Domain allow/block lists** (`federation.allowed_instances` /
`federation.blocked_instances`) are enforced at several seams:

- `POST /users/{username}/inbox` and `POST /ap/inbox` in
  `api/routes/federation.py` reject with 403 before queueing
  (`is_domain_allowed`). The shared-inbox route is Songhive's own — it
  shadows pubby's synchronous handler so deliveries queue onto
  `process_incoming` (with `username=None`) like per-user inboxes: reply
  materialization and notifications apply there too, whereas pubby's
  handler stopped at interaction storage and dropped non-public `Create`s
  entirely. For shared-inbox deliveries the notification recipients are
  resolved from the activity's audience
  (`federation/notifications.resolve_inbox_recipients`): local actor URLs
  in `to`/`cc`/`bto`/`bcc`, `Mention` tag targets, string `object`
  targets, and the local owners of objects referenced through
  `inReplyTo`, quote fields, or Like/Announce targets.
- `tasks/federation.py`'s `process_incoming` passes the lists to pubby's
  `InboxProcessor`, which drops the activity before signature verification.
- Pubby's outbound fan-out gets the same filtering via
  `allowed_instances`/`blocked_instances` on `ActivityPubHandler`
  (`_setup_federation` in `api/app.py`).
- `tasks/federation.py`'s `deliver_activity` drops outbound deliveries to
  blocked domains before signing.

**Remote discovery (explicit lookup):**

Songhive supports explicit, bounded lookup of remote actors, activities, and
resources — without crawling remote timelines or indexing the fediverse.

- `services/remote_content.py` classifies lookup input (`@user@domain`
  handles, actor/profile URLs, object/activity URLs, Songhive-style
  `/tracks/{id}` resource URLs) and enforces the `remote_search_access`
  policy (`disabled`/`authenticated`/`public`, default `authenticated`,
  editable at runtime by admins) plus the federation domain allow/block
  lists before any network access. Local-domain inputs never hit the
  network: `resolve_local_target` maps them straight to their SPA route
  (`/{kind}/{id}`, `/@{user}`, `/activities/{id}`; object permalinks
  resolve through `Track.federation_object_id` /
  `Activity.local_object_id`).
- All remote dereferencing goes through `federation/fetch.py`'s
  `guarded_fetch`: only `http(s)` URLs, DNS answers restricted to globally
  routable addresses, every redirect target revalidated (max 3 hops),
  bodies capped at 1 MiB, ActivityPub content types required, 404/410
  mapped to "gone". Each outbound request hop is bounded by
  `federation.fetch_timeout_seconds` (default 20s, 1–300;
  `SONGHIVE_FEDERATION__FETCH_TIMEOUT_SECONDS`, also admin-editable at
  runtime). When federation keys exist, each hop is signed with the
  instance actor key (HTTP signatures are regenerated per hop).
- Remote actors resolve through WebFinger and are cached in pubby's
  `federation_actor_cache` — a fetch-on-miss cache with freshness checks
  and tombstone markers for gone actors.
- Remote objects are cached in the `remote_objects` table (canonical URL,
  activity URL, domain, type, normalized resource kind, denormalized
  name/summary/content/media URLs, ETag/Last-Modified, content hash,
  `unavailable_at` tombstone). `Create`/`Announce`/`Update`/`Like`
  wrappers are unwrapped, `attributedTo` is validated against the
  publishing actor, and `Delete`/`Tombstone` documents mark the cached
  row unavailable instead of deleting it.
- Content objects materialize as `Activity` rows with
  `entity_type="remote"` pointing at their `RemoteObject` row and
  `source_type="remote"`/`source_id=<canonical URL>`; bare resource
  documents (e.g. a dereferenced `Audio` track) are cached but never
  become feed entries. Reply/quote parents resolve only against
  already-cached rows — remote threads are never fetched recursively.
- Routes: `GET /api/v1/remote/lookup` (explicit lookup → internal URL),
  `GET /api/v1/remote/actors/{handle}` (+ `/activities` for cached
  posts), `GET /api/v1/remote/objects/{id}` (`refresh` re-dereferences
  the canonical URL), and `GET /api/v1/remote/{kind}/{id}` for cached
  remote resources. Internal SPA URLs are `/@user@domain`,
  `/activities/@user@domain/{remote_object_id}`, and
  `/remote/{kind}/{id}` — remote URLs are never used as navigation
  targets.
- Aggregate search gains a cached-only `remote` section (`entities=remote`
  or the `include_remote` flag) that never performs network access, plus
  a `remote_available` flag telling the UI whether the caller may run an
  explicit lookup.

**Federated music entities (Funkwhale dialect):**

Songhive publishes and consumes a Funkwhale-compatible ActivityPub music
dialect, so music entities federate bidirectionally with Funkwhale
instances and with other Songhive instances.

- `federation/serializers.py` serializes `Artist`, `Album`, `Track`,
  `Audio` and `Library` documents under the `https://funkwhale.audio/ns`
  context (`MUSIC_ENTITY_CONTEXT`): artists and albums carry
  `musicbrainzId`/`released`/embedded `artists`, tracks carry
  `position`/`disc` and a nested `album`, and `Audio` uploads carry an
  integer `duration` plus `bitrate`/`size`, a `library` reference and the
  embedded `track` document — the fields Funkwhale's serializers require
  on import. `create_audio_activity` passes the publishing track's
  library URL (`/libraries/{id}` for a public `Library`, else the owner's
  implicit `{actor}/library` collection) so remote library followers
  receive `Create(Audio)` deliveries.
- Dereferenceable routes in `api/routes/federation.py` serve the music
  entities as ActivityPub JSON (HTML requests still get the SPA):
  `/artists/{id}`, `/albums/{id}` (public albums only), `/libraries/{id}`
  for public libraries — the collection index, with `?page=N` serving a
  `CollectionPage` of `Audio` items and `/followers` serving the object's
  follower collection — and `/users/{username}/library`, an implicit
  followable library of the user's public tracks. Actor documents
  advertise `endpoints.sharedInbox` and a `library` link to that implicit
  collection.
- `remote_content.py` recognizes Songhive resource URLs and Funkwhale's
  `/federation/music/{tracks,albums,artists,libraries,uploads}/{id}` and
  `/federation/actors/{name}` paths. Music documents classify to a
  normalized `resource_type` (`Audio`→track, `Track`→track,
  `Album`→album, `Artist`→artist, `Library`→library); `uploads` URLs
  normalize to `track`. Containment is recorded on the new
  `remote_objects.parent_url` column (`Audio`→library, `Track`→album,
  `Album`→first artist, collection pages→`partOf`), and entities embedded
  in a fetched document (`Audio.track`, `Track.album`, `Album.artists`,
  page `items`) are cached in the same table — bounded at 120 per fetch —
  so remote album/library pages render their contents without a fetch
  per child. `Library` documents additionally trigger one bounded fetch
  of their first collection page; a pasted `?page=N` URL resolves to its
  `partOf` collection. Funkwhale's `audience`-only public addressing is
  honored (`_doc_is_public`), and actor URLs that fail to dereference
  retry once through WebFinger.
- Remote resources can be **followed** object-scoped (FEP-efda style):
  `POST/DELETE /api/v1/remote/objects/{id}/follow` stores a `follows`
  row keyed on the object URL and delivers `Follow`/`Undo(Follow)` to the
  object's controlling actor inbox (`actor`/`attributedTo`, e.g. a
  Funkwhale library's channel actor). Inbound `Accept`/`Reject` are
  matched in `apply_follow_decision` against the followed object *or* its
  owning actor. Inbound `Follow`s of a local object delivered to the
  shared inbox are processed under the object's owning user actor
  (`_object_follow_owner_username` in `tasks/federation.py`) so the
  `Accept.actor` matches the library's controlling actor — which Funkwhale
  requires. Inbound `Create`/`Announce`/`Update` admission accepts
  activities whose object is — or names as its container (`library`,
  `context`, `target`) — a followed remote resource, in addition to the
  followed-actor rule.
- Remote resources can be **collected**: `item_type="remote"` on
  `POST/DELETE /api/v1/collection/{item_type}/{item_id}` bookmarks a
  cached `remote_objects` row by id — only rows with a `resource_type`
  are collectable, and nothing is copied into local music tables.
  `GET /api/v1/remote/objects?collection=true&resource_type=...` lists
  them (authenticated), and `RemoteCollectionSection.vue` renders them
  inside the Tracks/Albums/Artists/Libraries/Playlists collection views.
- Remote media **resolution** happens at play time, never at cache time:
  a remote *track* is metadata whose media can be a direct `audio/*`
  link (`audio_url`) or a *rendition* sibling (`Audio`/`Video` document
  embedding `track`). Rendition rows record that link on the indexed
  `remote_objects.media_of_url` column (`Audio`→`track.id`), so
  `media_of_url == track.canonical_url` resolves without payload scans
  (a bounded JSON-path fallback covers rows cached before the column
  existed). `GET /api/v1/remote/objects/{id}/stream` resolves through
  `remote_content.resolve_media_url` — own `audio_url` first, else a
  cached rendition — and 302s to the media URL (404 when nothing is
  playable). Responses advertise `stream_url` only when something is
  resolvable now; it is computed in one batched `rendition_audio_map`
  query per response. This endpoint is the seam for providers whose
  links expire or require resolution (e.g. future YouTube/Spotify
  adapters): they plug into `resolve_media_url` and clients never
  handle a remote URL directly.
- Remote object responses (`RemoteObjectResponse`) carry
  `in_collection`, `follow_state`, `parent` and `items` (cached
  children), and `RemoteResourceView.vue` renders them with follow and
  collection `EntityActions`, a parent link and a contents list.

---

## Content Moderation

- `Report` model stores user-submitted content flags (target type/id, reason,
  description, status, reviewer). Actor reports (`target_type` `user`/`actor`)
  accept a username, `@user@domain` handle or actor URL, are normalized to
  `target_type="user"`, and store the reported account's canonical actor URL in
  `target_actor_url` (`target_id` holds the local user id or the remote actor
  URL; legacy callers may still pass a bare user id). The `forwarded` flag
  records whether the report was relayed to a remote instance.
- Public submission: `POST /api/v1/reports`. `forward: true` on a remote actor
  report enqueues an ActivityPub `Flag` activity to the reported actor's inbox
  via `tasks.federation.deliver_activity` (requires the reporter's federation
  keys; otherwise the report is stored with `forwarded=false`).
- Every report notifies all active admins (except the reporter) through a
  `report` notification carrying `report_id`/`target_type`/`target_actor_url`/
  `reason`; the frontend links it to `/admin/reports`.
- Admin review: `GET/PUT /api/v1/admin/reports`. Responses include
  `reporter_username`, `target_actor_url` and `forwarded`.
- `AuditLog` records administrative and security-relevant actions (actor, target
  type/id, IP address, JSON details). `target_type` values are defined by the
  `AuditTargetType` enum (`models/audit_log.py`) — `log_action` only accepts
  enum members, and `GET /api/v1/admin/audit/target-types` exposes them to the
  admin UI's target-type filter.

### Actor and instance moderation

Mastodon-style moderation lives in three tables (`models/moderation.py`) and
one service (`services/moderation.py`):

- `UserModeration` — per-user `mute`/`block` on any actor (local or remote),
  keyed on the canonical actor URL (`user.actor_url`, or the
  `urn:songhive:user:<name>` fallback when federation is disabled). A mute is
  one-way: the target's activities leave the muter's feeds but the muter's
  activities still reach the target. A block is reciprocal: it hides both
  directions, prevents interaction both ways, and severs every follow
  relationship between the pair (`sever_block_relationship` removes the
  `follows` rows and the pubby follower/request records).
- `AdminUserModeration` — instance-wide `limit`/`suspend` on an actor with an
  optional `reason`, unique per actor URL (applying a new action upgrades the
  existing row). `limit` forces the actor's follows of local users through
  manual approval (`follow_user` passes `force_manual`) and gates their
  activities to their own followers — on timelines and on their profile
  page, which shows a "limited by the moderators" notice and a "show
  anyway" button that re-fetches the timeline with `reveal=1` (the
  `reveal` query param lifts only the followers-only gate for that actor;
  hidden/defederated content stays excluded). `suspend` additionally blocks all
  interaction (`assert_interaction_allowed`), drops inbound activities
  (`tasks/federation.process_incoming`), removes the actor from notification
  fan-out, severs every local follow relationship (`sever_relationships`),
  and hides all of the actor's content.
- `InstanceModeration` — per-domain `defederate`/`followers_only` policies
  with an optional `reason`, layered over the configured
  `federation.allowed_instances`/`blocked_instances` lists. Because the
  synchronous federation paths (Celery delivery, pubby inbox resolution,
  remote-actor search) cannot query the database, `load_instance_policies`
  installs a process-local snapshot (`federation.set_db_instance_policies`)
  that `is_domain_blocked`/`db_domain_policy` consult — a `defederate` row
  behaves exactly like a configured block, while `followers_only` gates the
  domain's activities to local users who follow the author.

Visibility is enforced through `moderation_context` (one small set of queries
per request) + `moderation_filter`/`activity_hidden`, applied to activity
lists, single-activity views, timelines, search results, and remote-actor
lookups. Interaction endpoints run `assert_not_suspended`/
`assert_interaction_allowed`; notification fan-out checks
`notification_suppressed`, which also drops filterable notifications
(everything but `report`) from limited actors and followers-only-domain
actors when the recipient does not follow them — Mastodon's
`for_limited_accounts: drop` policy. Federated dereference endpoints return 404 for
suspended owners, and defederated domains are dropped inbound (inbox
processing, remote materialization) and outbound (delivery domain checks,
audience resolution).

API surface:

- `GET/POST/DELETE /api/v1/users/me/mutes` and `.../blocks` — personal
  moderation lists (`ModeratedActorResponse` carries a display snapshot).
- `GET/POST/DELETE /api/v1/admin/moderation/users` — admin limit/suspend.
- `GET/POST/DELETE /api/v1/admin/moderation/instances` — admin domain policies.
- `GET /api/v1/instance/domain_blocks` — Mastodon-compatible transparency
  endpoint mapping `defederate`→`suspend` and `followers_only`→`silence`.
- `GET /api/v1/users/{username}` and `/api/v1/remote/actors/{handle}` report
  `muted`/`blocked` (viewer-relative) and `limited`/`suspended` (instance)
  flags; profile pages show the badges plus mute/block actions for signed-in
  viewers and limit/suspend/clear actions for admins.
- `/settings?tab=moderation` lists the viewer's mutes/blocks with undo;
  `/admin/moderation` manages all admin actions with optional reasons.
- All admin mutations record `moderation.<verb>` `AuditLog` entries.

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

### Embeddables

Public entities can also be embedded into third-party pages from the share
dialog's "Embed" tab (`components/share/EmbedPanel.vue`). Four snippet
formats are generated client-side (`utils/embed.ts`):

- Tracks default to a standalone `<audio>` tag wrapped in a `<p>` with the
  track metadata linked back to the Songhive page. For albums, artists,
  playlists, and libraries this is replaced by a `<div>` listing one
  `<audio>` element per track — only offered when the collection holds
  fewer than 250 elements, and limited to public tracks with a playable
  `audio_url`.
- A Markdown link `[{artist} - {title}]({url})` (title or filename
  fallbacks for tracks, the entity name for collections).
- A `<script>` embed: a `data-songhive-embed` placeholder rendered by
  `frontend/public/embed.js` (served as `/embed.js`) into an iframe of the
  SPA's standalone `/embed/{type}/{id}` route (`views/EmbedView.vue`) — a
  compact track player for tracks and an expandable tracklist for
  collections. The embed page reports its content height via `postMessage`
  and the script resizes the iframe to match.
- A no-JS `<iframe>` fallback pointing at the same `/embed/{type}/{id}`
  route with fixed heights.

Embed URLs load anonymously, so the tab only offers snippets for public
items (owners see a publish-first hint otherwise).

---

## Feeds

`api/routes/feeds.py` serves RSS 2.0 and Atom 1.0 documents under `/feeds`
(outside `/api/v1`, like `/webmentions` and `/ap`), rendered by
`services/feeds.py` with standard-library XML escaping — no feed library is
involved. Each feed is available in both formats by swapping the URL suffix:

| Feed URL                                        | Content                                             |
|-------------------------------------------------|-----------------------------------------------------|
| `/feeds/users/{username}.{fmt}`                 | The user's latest posts (`mode="posts"` timeline)   |
| `/feeds/{plural}/{id}/activities.{fmt}`         | Activities attached to a track, album, artist, playlist, library or radio |
| `/feeds/artists/{id}.{fmt}`                     | Latest releases: albums plus standalone tracks merged by date |
| `/feeds/playlists/{id}.{fmt}`                   | Tracks most recently added to the playlist          |
| `/feeds/libraries/{id}.{fmt}`                   | Tracks most recently added to the library           |
| `/feeds/tags/{name}.{fmt}`                      | Newest entities and activities carrying the tag     |
| `/feeds/genres/{name}.{fmt}`                    | Newest tracks and albums in the genre               |

`{fmt}` is `rss` or `atom` (served as `application/rss+xml` /
`application/atom+xml`); `?limit=` may reduce the page size but never
exceed `feeds.max_items` (default 20, range 1–500). The whole subsystem is
gated on `feeds.enabled`. Feed queries reuse the same ACL predicates as the
JSON API, so anonymous readers only see public content and authenticated
requests also cover `local` visibility and share grants; a valid share
token still grants access to a private collection's feed. For the artist
releases feed, a track that belongs to an album visible to the requester is
not listed separately — the album entry already represents it. Track items
carry an `<enclosure>`/Atom `rel="enclosure"` link pointing at the public
`/api/v1/stream/{id}` URL when the track has audio.

Feed discovery happens server-side so non-browser clients can find the
URLs: `api/semantic_meta.py` appends
`<link rel="alternate" type="application/rss+xml|atom+xml">` tags to the
semantic `<head>` tags it injects into the SPA shell for object pages (and
`profile_pages` does the same for `/@{username}`), mirroring whatever feed
the page exposes — track and album pages advertise their activities feed,
`/activities` sub-pages of artists/playlists/libraries switch to the
activities variant. On the frontend, `utils/feeds.ts` builds the same URLs,
`composables/useFeedLinks.ts` keeps `document.head`'s feed alternates in
sync after client-side navigation (replacing the tags the server injected
for the landing page and removing them on feed-less pages), and
`components/ui/FeedButton.vue` renders an RSS/Atom menu on every
feed-backed page (entity headers, activity pages, tag/genre detail and
user profiles).

---

## Notifications

`models/notification.py` defines `Notification` (recipient `user_id`,
`type`, optional `actor_url`/`source_url`, JSON `payload`,
`delivered_targets`, `seen_at`, `digest_sent_at`) and
`NotificationPreference` (unique per `(user_id, type)` with `in_app`,
`email`, and `email_digest` toggles). The notification types are
`follow`, `like`, `boost`, `quote`, `reply`, `mention`, `share`,
`webmention`, `activity`, and `report` (admin-only: a user report was
filed — links to `/admin/reports`). `ActivitySubscription` rows
record that a local user wants an `activity` notification for every
activity another actor authors — the "bell" toggle on a user profile.
Local users are referenced through `target_user_id`, remote actors through
`target_actor_url` (exactly one is set per row, each unique per
`(user_id, target)` pair, no self-subscriptions).

`services/notifications.py` creates notifications: it resolves the
recipient's per-type targets (defaults when no preference row exists are
in-app on, email and digest off), skips creation entirely when all targets
are disabled, and deduplicates on `(user, type, actor_url, source_url)`
against existing unseen rows. When `in_app` is enabled it pushes a
`notification` event over the WebSocket bus via
`EventWebSocket.send_to_user`, delivered only to the recipient's
connections. When `email` is enabled and the recipient has a verified email
address it enqueues `tasks/email.py`'s `send_notification_email`. Rows with
`email_digest` enabled are collected by the scheduled
`send_notification_digest` task, which groups pending rows per user, sends
one plain-text email, and stamps `digest_sent_at` only after a successful
send. `purge_seen_notifications` deletes seen rows older than
`notifications.retention_days` (default 90). Hook and email failures are
logged and swallowed so they never break the originating operation.

The same module also removes and rewrites notifications:
`delete_notifications` and `clear_notifications` handle recipient-scoped
manual deletion, while `retract_notifications` (filtering on recipient,
type, actor URL variants, and `source_url`) and
`retract_notifications_referencing` (matching `source_url`,
`payload.target_url`, and `payload.activity_id`; dialect-aware for
PostgreSQL vs SQLite/MySQL JSON extraction) clean up rows whose
underlying event was undone or deleted. Every deletion path pushes a
`notification_deleted` event (`{"ids": [...]}`) to each affected
recipient over the WebSocket bus. `update_notifications_referencing`
rewrites the managed snapshot fields of rows referencing a set of
activity/object URLs, `update_notifications_from_actor` refreshes actor
metadata (`actor_name`/`actor_display_name`/`actor_avatar_url`) on rows
produced by a given actor, and `refresh_notifications_for_item` updates
`item_title`/`target_item_title`/`track_title` on rows referencing a
local item; each pushes
a `notification_updated` event carrying the changed rows. Updates only
touch managed fields — unrelated payload keys and read/unread state are
preserved, and missing source fields remove stale keys. Service helpers
flush but never commit.

REST API (`api/routes/notifications.py`; every endpoint is authenticated
and scoped to the current user):

| Route | Description |
|-------|-------------|
| `GET /api/v1/notifications/` | Newest-first list, `seen` filter, `type` CSV allowlist, `limit`/`offset` + `X-Total-Count` |
| `GET /api/v1/notifications/unread-count` | Unseen count for the nav badge |
| `POST /api/v1/notifications/seen` | Mark ids seen |
| `POST /api/v1/notifications/unseen` | Mark ids unseen |
| `POST /api/v1/notifications/seen-all` | Mark all current-user rows seen |
| `DELETE /api/v1/notifications/{id}` | Delete one owned notification |
| `POST /api/v1/notifications/delete` | Bulk delete owned ids (max 500) |
| `POST /api/v1/notifications/clear` | Delete all current-user rows |
| `GET`/`PUT /api/v1/notifications/preferences` | Merged per-type delivery matrix |

`POST /api/v1/admin/notifications/purge` performs a manual retention purge
and records a `notification.purge` audit row with the deleted count and
retention window; `songhive admin purge-notifications` runs the same service
from the CLI, and the `/admin/tasks` page exposes the endpoint as a
confirmation-protected action.

Creation hooks: favoriting another user's track
(`api/routes/favorites.py`) creates a `like`, and liking another local
user's activity (`services/activities.py`'s `like_activity`, reached via
`POST /activities/{id}/like`) notifies its `owner_user_id`; creating a new
share grant (`api/routes/shares.py` via `services/sharing.py`'s
`(grant, created)` return) creates a `share`. The federation inbox path
(`tasks/federation.py` → `federation/notifications.py`) maps Follow/Like/
Announce/Create/QuoteRequest activities for each resolved local recipient
— the addressed user for per-user inboxes, `resolve_inbox_recipients`'
audience resolution for shared-inbox deliveries — `quote` wins over
`reply` when a Create is both, only the target's owner gets the
`reply`/`quote` notification (a reply or quote of someone else's post
that merely tags the recipient stays a `mention`, which is otherwise
suppressed once a reply/quote notification for the same note fired),
and a `Like`/`Announce` likewise only notifies the target's owner (a
followed actor's like or boost of somebody else's — or a remote — post
notifies nobody: the interaction is pubby's `federation_interactions`
record plus, for followed actors' announces, a materialized `announce`
row) — and stamps matching `ActivityMention.notified_at` rows. A FEP-044f
`QuoteRequest` (auto-approved by pubby, which stores a dereferenceable
`QuoteAuthorization` and answers `Accept`) also yields a `quote`
notification when its `object` resolves to a local post owned by the
recipient — the quote's own `Create` may never reach the inbox — with the
`instrument` id recorded as `source_url`. Because a quote can arrive
twice (request then `Create`, or the same `Create` on two inboxes) and
the first row may already be seen, `quote` notifications deduplicate and
merge payloads on `(recipient, actor, quoting object)` regardless of
seen state, preserving the existing row and its read marker
(`_create_or_update_quote_notification`).

Activity subscriptions fan out through
`services/activities.py`'s `_notify_activity_subscribers`, invoked by every
local producer path (`create_local_activity` for `create` types,
`create_status`, `record_track_publication`, `like_activity`,
`boost_activity`, `reply_to_activity`, `quote_activity`). Each subscriber
is filtered through `can_view_activity` — `mentioned`/`private` posts and
inaccessible entities never leak — and the author, the interaction
target's owner, and already-mentioned users are skipped so the `activity`
row stays a fallback rather than a second notification for an event a
specific `reply`/`quote`/`like`/`boost`/`mention` row already covers.
The payload snapshots the authored activity (or, for authored
likes/boosts, the reacted one) with the same `object_*`/`target_*`/
`item_*` fields the other hooks produce, plus `activity_type` so clients
can phrase the action ("shared a post", "liked a track").

Remote actors fan out the same way through
`notify_remote_activity_subscribers`, keyed on the materialized row's
`source_actor`: it is invoked by the inbox materializers —
`_materialize_remote_object` (replies and quotes),
`materialize_remote_announce`, and `materialize_remote_post` via a
`notify_subscribers` flag on `_materialize_remote_activity`, so only
inbox-delivered activities notify while explicit remote-object lookups do
not. Actor display fields come from the federation actor cache
(`resolve_source_actor_profiles`) with a `user@domain` handle fallback,
and `remote`-entity rows link the local `/activities/{id}` page since they
resolve no local entity. A remote actor `Delete` drops the subscriptions
targeting them.

Subscription state is toggled via
`POST`/`DELETE /api/v1/users/{username}/activity-subscription` for local
users and `POST`/`DELETE /api/v1/remote/actors/{handle}/activity-subscription`
for remote actors; subscribing also follows the target on a best-effort
basis (a failed follow does not fail the subscription) and the response
reports the resulting `follow_state`. The viewer's state is surfaced as
`activity_subscribed` on the public profile and remote-actor responses;
account deletion drops the rows in both directions, including any keyed on
the deleted user's actor URL.

Notification `payload`s are denormalized at creation so rows stay renderable
after the source object disappears. Every hook records the actor's
`actor_name`/`actor_display_name`/`actor_avatar_url` (the federated values
come from pubby's cached actor document); likes, boosts, and shares record
`item_type`/`item_id`/`item_title`/`local_url` when the object resolves to a
local entity (`federation/notifications.py`'s `_resolve_local_object`
matches `{actor}/objects/{id}` against `Track.federation_object_id` and
`Activity.local_object_id`/`source_id`, and `/{plural}/{id}` URLs on the
instance domain). Objects resolving to a local `Activity` additionally
record `object_activity_id`, the ActivityStreams `object_type` (`Note`,
`Audio`, …) and `object_page_url` — the activity's own page, i.e. the
entity's activity feed or the author's profile for `user` entities
(`services/activities.py`'s `activity_page_url`, shared by
`_entity_link_fields` and the object-permalink browser redirects). `user`
entities have no item page, so `item_type`/`item_id` are omitted there and
`local_url` points at the author's `/@username` profile. `Create` payloads carry a `_note_snapshot` —
`object_content` (capped raw HTML), `object_summary`, `object_name`,
`object_url`, `published`, `object_mentions` — and replies/quotes add
`target_url` plus `target_*` fields for a resolved local target. The
`RE: <link>` quote fallback remote servers embed for non-quote-aware
clients (a `quote-inline` element for Mastodon/Akkoma, a bare `RE: <url>`
tail for Misskey/Threads) is stripped from stored content and snapshots
(`strip_quote_fallback`) — the quoted activity renders through the
`in_reply_to_activity_id` embed instead.

Retraction mirrors creation so notifications don't outlive their event:
unfavoriting a track retracts the owner's `like` notification, retracting a
`like` activity removes the notification it produced (matched via
`payload.activity_id`), revoking a share grant retracts the grantee's
`share`, `retract_activity` and `cascade_delete_entity` retract
notifications referencing the removed activity/entity, and deleting a user
retracts the notifications they produced elsewhere. On the inbox path, `federation/notifications.py`'s
`retract_inbox_notifications` handles `Undo` (Follow/Like/Announce,
including undo-by-activity-id via `payload.activity_id`) and `Delete`
(object or actor) scoped to the addressed recipient.

Edits propagate too: `update_activity` and `sync_track_publications`
refresh the object snapshot on notifications referencing the rebuilt
object (retracting rows whose mention/reply/quote basis no longer
holds), and track renames refresh the stored item titles. On the
inbox path `update_inbox_notifications` handles `Update` activities —
a `Note` update refreshes the snapshot when the notification is still
applicable and retracts it when the recipient is no longer mentioned or
the reply/quote target changed away, while an actor `Update` refreshes
the stored actor metadata.

The frontend `stores/notifications.ts` owns the unread count, the paginated
list, and optimistic seen/unseen and deletion updates (with rollback +
toast on error). The list composes an `all`/`unread` read-state filter with a
multi-select type allowlist (empty = all types) sent as the `type` CSV param;
incoming events of filtered-out types still bump the badge but stay off the
list.
It registers `notification`, `notification_deleted`, and
`notification_updated` handlers on the
shared `eventBus` singleton
(`api/ws.ts`), connected when the auth store reports authenticated and
disconnected on logout; each received notification also raises an info
toast naming the actor and action, while deletions prune matching rows
and updates replace changed rows in place, both silently. `AppLayout` renders a `99+`-capped badge on the
`/notifications` nav item; `views/NotificationsView.vue` auto-marks visible
rows seen via a debounced `IntersectionObserver` batch that defers to rows
the user just toggled back to unseen, and offers per-row dismissal plus a
TrackList-style selection mode for bulk delete/read/unread and a
confirmation-protected "Clear all". Each row's header links the actor name
to the actor's internal profile (local or remote `/@name@host`) and the
action text separately
to the referenced object — for likes/boosts the `object_page_url` of the
reacted activity. The action text names the interacted entity
(`utils/notifications.ts`, shared with the WebSocket toast): likes/boosts
on `Audio` objects or resolved `item_type`s render as "liked/boosted your
track/album/…", replies/quotes do the same through their `target_*`
fields, and shares name the granted item ("shared an album with you"),
while `Note` objects and unresolved remote objects keep the
generic "post" wording. Rows render context cards from the
payload: follows (and unresolved likes/boosts) show
`components/notifications/NotificationActorCard.vue` (all actors route to
the internal profile — `/@name` locally, `/@name@host` for remote actors,
whose profile keeps the link out to the origin site),
mentions/replies/quotes embed a
read-only `ActivityCard` fed by the note snapshot (remote HTML reduced to
plain text), likes/boosts on `Note` objects fetch and embed the real
`ActivityCard` through `NotificationActivityCard.vue` (`GET
/api/v1/activities/{id}`), and shares plus likes/boosts on `Audio` objects
(or failed activity fetches) show
`NotificationItemCard.vue`, which fills in title and cover art through the
deduplicating `composables/useItemSummary.ts` cache when the payload lacks
them. The `/settings?tab=notifications`
profile tab (`views/profile/NotificationSettings.vue`) edits the per-type
in-app/email/digest matrix; the email columns are disabled until the
account email is verified.

### Mentions archive

Mention notifications are dismissible and purged by retention, and they
are never created at all when the recipient's delivery preferences drop
in-app rows or a reply/quote notification already covered the note — so
they cannot answer "which activities ever mentioned me". The mention
archive closes that gap: `models/mention_record.py` defines
`MentionRecord` (recipient `user_id`, `source`, `source_url`,
`activity_id`, `actor_url`, `visibility`, JSON `payload`), a permanent
per-user record of every activity that addressed them, backed by
`services/mention_records.py`. Rows are keyed on
`(user_id, source, source_url)` so re-delivery and edits upsert instead
of duplicating, and `payload` snapshots the same render fields the
matching notification carries (actor identity, `object_*` snapshot,
`target_*`/`item_*`/`local_url` link fields) so records stay renderable
after the source object disappears.

`source` identifies the pipeline that produced the mention and each one
writes records independently of notification delivery:

- `local` — activities authored on this instance
  (`services/activities.py`'s `_record_activity_mentions`, run by
  `create_local_activity`, `create_status`, `reply_to_activity`, and
  `quote_activity`). Every mentioned local user is recorded — including
  self-mentions and recipients already covered by a reply/quote
  notification — with the activity's `source_id` as `source_url`, its
  materialized `activity_id`, and its stored `visibility`.
- `activitypub` — objects received through the federated inbox
  (`federation/notifications.py`'s `_record_inbox_mention`, called from
  `create_inbox_notifications` whenever the note's `Mention` tags
  address the recipient). Visibility is classified from the object's
  addressing: `public` when it addresses the public collection,
  `followers` when the author's followers collection is addressed,
  `mentioned` otherwise.
- `webmention` — incoming Webmentions materialized into activities
  (`webmentions/service.py`'s `_record_webmention`, called from
  `materialize_webmention` for the target's owner). The deterministic
  `urn:songhive:webmention:<hash>` activity `source_id` keys the record,
  so re-sent mentions refresh it and the target's visibility is
  mirrored.

The lifecycle mirrors the notification one so records don't outlive
their event: local edits (`update_activity`) re-resolve mentions and
delete records for recipients the edit dropped, `retract_activity` and
`cascade_visibility_update` remove or reclassify records with the
activity, federated `Undo`/`Delete` retractions
(`retract_inbox_notifications`) remove records referencing the undone or
deleted object/activity — or every record a deleted actor produced —
and `Update` handling (`update_inbox_notifications`) merges the revised
snapshot into surviving records, drops records whose `Mention` tag was
edited out, and refreshes `actor_*` fields when the actor document
itself is updated. Webmention retractions flow through
`retract_activity`. Deleting a user removes their archive and the
records their actor URL produced (`users/manager.py`'s
`_remove_user_references`).

REST API (`api/routes/mentions.py`): `GET /api/v1/mentions/` is
authenticated and scoped to the current user, newest-first, with
`limit`/`offset` + `X-Total-Count` pagination, a `source` CSV allowlist
(`local`, `activitypub`, `webmention`; unknown values ignored), and
`visibility=private` restricting to non-public records.

The frontend `/mentions` route (`views/MentionsView.vue`, authenticated)
lists the archive with source and all/private filter button groups and
load-more pagination. Each row reuses the notification card pipeline —
a real `ActivityCard` through `NotificationActivityCard` when
`activity_id`/`object_activity_id` resolves to a stored activity, a
read-only snapshot `ActivityCard` for unmaterialized remote notes, then
`NotificationItemCard`/`NotificationActorCard` fallbacks — and badges
the source, the Webmention type, and a lock for non-public records.

---

## Task Queue (Celery)

All background work is handled by Celery workers. Redis is the broker
(db 1) and result backend (db 2).

| Task module          | Responsibilities                                          |
|----------------------|-----------------------------------------------------------|
| `tasks/import_.py`   | File processing, tag extraction, track/album/artist upsert|
| `tasks/federation.py`| Activity delivery, inbox processing, key provisioning, remote-activity pruning |
| `tasks/transcoding.py`| Pre-transcode to common formats, cache result            |
| `tasks/email.py`     | Verification, password-reset, notification emails          |
| `tasks/notifications.py`| Notification digest + seen-notification retention purge (scheduled) |
| `tasks/musicbrainz.py`| MusicBrainz + Cover Art Archive metadata enrichment      |
| `tasks/images.py`    | Artist image + Cover Art Archive cover enrichment         |
| `tasks/preview_cards.py`| Fetch + cache per-URL link-preview cards for activities  |
| `tasks/storage.py`   | Orphaned `StoredFile` GC, audio-only hash rehash (scheduled) |

The `cleanup_orphaned_files_schedule` config accepts any 5-field cron
expression. The notification digest and retention purge run on fixed daily
crontabs at `notifications.digest_hour` (default 8 AM) and
`notifications.purge_hour` (default 3 AM).

`songhive.tasks.federation.prune_remote_activities` removes stale remote
content through `services.remote_content.prune_stale_remote_activities`:
`source_type="remote"` activities whose `published_at` is older than
`federation.remote_activity_retention_days` (default 30, overridable via
the task's `older_than_days` argument) and that no local user has
interacted with — a live like/boost/reply/quote child reached through
`in_reply_to_activity_id` protects the whole thread, so the eligible set
is computed to a fixpoint. Mirrored `remote_objects` cache rows are
removed with their activities and can always be re-fetched via remote
URL search; bare remote resources without an activity row are never
touched. The task runs manually via `POST
/api/v1/admin/federation/prune-remote-activities` (audited as
`federation.prune_remote_activities`), `songhive admin
prune-remote-activities`, or the admin Tasks page — all with dry-run
support — and only joins the beat schedule when
`federation.remote_activity_prune_schedule` holds a 5-field cron
expression.

Each task's async work is executed with ``asyncio.run(...)``. Because
``asyncpg`` connections are bound to the event loop that created them, every
task disposes the shared async engine and resets the global session factory
before the loop closes, ensuring the next task gets a fresh pool.

---

## Email

SMTP-based email is configured via the `email` config section. Celery tasks
in `tasks/email.py` enqueue verification, password-reset, and individual
notification messages asynchronously; `tasks/notifications.py` sends the
per-user daily digest. `EmailNotConfiguredError` is raised when the SMTP
host or `from_address` is missing.

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
   tag associations via `genres_to_tags`, and propagates the album's
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

- Validates the `Origin` header against `config.server.cors_origins`; origins
  matching the request `Host` are always allowed since the SPA and `/ws` are
  normally served same-origin (nginx proxies both, and Vite forwards `/ws` in
  development).
- Authenticates the connecting user via a JWT access token: the `?token=`
  query parameter (API clients) takes precedence, then the `access_token`
  `HttpOnly` cookie, which browsers send automatically on same-origin
  handshakes.
- Broadcasts real-time events (import progress, federation notifications, etc.)
  to authenticated clients.
- `EventWebSocket.send_to_user(user_id, event_type, data)` serializes events
  the same way as `broadcast` but delivers them only to connections owned by
  the given user; it is used for the targeted `notification` event pushed by
  the notification service.

`broadcast`/`send_to_user` can only see the connections living in their own
process, so both also publish an envelope to the Redis pub/sub channel
`songhive:ws-events` (via a synchronous client — Celery tasks run each job in
a fresh `asyncio.run` loop). The Tornado process runs `ws_event_subscriber`
on its IOLoop, which delivers envelopes from other processes through
`EventWebSocket.deliver_envelope`; envelopes stamped with this process's id
are skipped since they were already delivered locally. This is what makes
notifications created in the Celery worker (federated follows, likes, etc.)
reach connected clients live.

The frontend `EventBus` (`frontend/src/api/ws.ts`) reconnects dropped sockets
with exponential backoff (1s up to 30s). Since the server authenticates after
the upgrade, the backoff only resets once a connection has stayed open for a
while; a close with code `4001` (unauthenticated) additionally triggers an
access-token refresh so the retry uses fresh credentials instead of looping
on a stale token.

Because authentication happens after the handshake, the browser sees an
accepted socket that is then closed — which resets the reconnect backoff of
older clients on every attempt. To bound that churn server-side, the handler
tracks authentication failures per `(remote IP, token digest)`: the first
failure still closes immediately with `4001`, but each consecutive failure
delays the close exponentially (1s, 2s, …, capped at 30s).

---

## Frontend

Vue.js 3 + TypeScript SPA, bundled with Vite.

| File/Dir | Role |
|----------|------|
| `frontend/src/main.ts` | App bootstrap, Pinia + i18n + router mount, theme apply |
| `frontend/src/App.vue` | Root component (`<RouterView />`) |
| `frontend/src/router/` | Vue Router (history mode) with global auth/admin guard |
| `frontend/src/stores/` | Pinia stores (auth, theme, toast, confirm, player, playback session, outputs) |
| `frontend/src/components/ui/` | Headless base components (button, input, select, avatar, table, pagination, search, context menu, entity actions) |
| `frontend/src/components/feedback/` | Toast, banner, spinner, skeleton, modal, confirm dialog |
| `frontend/src/components/entity/` | Reusable entity grid/list components (e.g. `BulkEditableGrid` for bulk selection and deletion) |
| `frontend/src/components/activities/` | Activity feed components (`ActivityFeed` filter tabs + cursor pagination, `ActivityCard`, `ActivityEditModal`) backed by `stores/activities.ts` and `api/activities.ts`. `ActivityAudioPlayer` renders `Audio` attachments as a styled inline player (artwork — attachment `image`, then the author's avatar, then a note icon — title/artist/album, seek and volume controls) instead of the browser-default element; its "Play in player"/"Add to queue" actions resolve local `songhive:trackId` attachments through the tracks API into `QueueTrack`s, while federated audio is synthesized as a `remote` queue track that streams its media URL directly. All embedded activity players share a module registry so starting one pauses the others and the global player, keeping a single audio source at a time |
| `frontend/src/components/statuses/` | `StatusComposer` — shared status editor (plain text or Markdown, visibility, BCP-47 language defaulting to the browser locale, `@` mention and `#` hashtag autocomplete (hashtags sorted by popularity; mentions also cover remote actors via the `remote_users` search flag and accept `user@domain` narrowing) plus track-only attach search via `SearchBar`/`SearchSuggestions`, file uploads through `api/files.ts`). Posts through `api/statuses.ts` (`POST /statuses/`) by default; the share dialog's Fediverse tab injects a custom submit that calls `tracks.publishTrack` instead, and `ActivityEditModal` reuses it for edits (`initial*` props seed the existing text/format/language/attachments; attachment chips map to `songhive:fileId`/`songhive:trackId`-marked docs). |
| `frontend/src/components/user/` | Reusable user display components (`UserLink`) used across activity cards, resource owner metadata, file details, audit logs, and admin lists. `UserLink` renders local users as `RouterLink`s to `/@{username}`, remote users as external links to `actor_url`, and accepts either a full `UserSummary` owner or legacy `username`/`displayName`/`avatarUrl`/`remoteUrl` props |
| `frontend/src/components/admin/` | Admin-specific shared components (e.g. `StatCard` for the dashboard) |
| `frontend/src/components/player/` | Persistent player bar (`PlayerBar`, `NowPlaying`, `QueuePanel`, `VolumeControl`, `OutputSelector`) mounted in `AppLayout` so playback survives route changes, backed by `stores/player.ts` and the singleton `player/engine.ts` (dual `HTMLAudioElement` primary/preload). `OutputSelector` routes playback between "This device" and configured server-side outputs via `stores/playback.ts`; output management lives in the profile Outputs tab (`views/OutputsView.vue`, `stores/outputs.ts`). `QueueTrack` extends `TrackResponse` with `stream_url` — a direct media URL used instead of `/api/v1/stream/{id}` for audio without a local track row — and `remote`, which suppresses library links and listen-history reporting |
| `frontend/src/layouts/` | App, auth, and admin layouts |
| `frontend/src/views/` | Page-level components, including `views/admin/` (Dashboard, Users, Settings, Reports, Invites, Audit, Tasks, Celery) behind the `/admin` guard (Home, Library, Album/Artist/Track/Playlist lists and details, History, Favorites, Files, File detail, Radio station list/create/play, About, Login, Register, PasswordReset, VerifyEmail, `/settings` for the authenticated user, `UserProfileView` for `/@{username}`, `UsersDirectoryView` for `/users`, `SearchView` for the public `/search` page, plus 403/404 and placeholder views). `SearchView` renders grouped, independently sortable and paginated sections for every searchable entity via `useSearchSections`; the shared `SearchBar` supports an optional autocomplete mode backed by the aggregate `/api/v1/search/` endpoint, with `SearchSuggestions` offering arrow-key navigation (Enter picks the highlighted item) via an exposed `handleKeydown` hook the controlling input forwards to. When the caller passes `remote` (the `remote_available` flag from the search response) and the query looks federated — an `https://` URL or an `@user@domain` FQN — `SearchSuggestions` appends a "See on the Fediverse" entry that emits `remote-lookup`; `SearchView` routes it to `/remote/lookup`. `UserProfileView` renders user bios through the `RichText` component, which linkifies hashtags, mentions and URLs; library, playlist, album, and track detail views render the owner through the `UserLink` component |
| `frontend/src/api/` | Typed HTTP client (`openapi-typescript` generated `types.ts`), per-resource modules including `admin.ts` for the admin panel, WebSocket event bus, stream URL helper |
| `frontend/src/i18n/` | `vue-i18n` setup with lazy-loaded locales |
| `frontend/src/styles/tokens.css` | CSS custom properties for theming |

Browser authentication is cookie-based: the SPA never stores JWTs. The
`api/client.ts` fetch wrapper sends `credentials: "same-origin"` on every
request, echoes the readable `csrf_token` cookie as `X-CSRF-Token` on unsafe
methods, and retries once after a cookie refresh on 401. The auth store
(`stores/auth.ts`) persists only the non-sensitive user profile and bootstraps
by fetching `/users/me`; stream URLs (`api/stream.ts`) and the WebSocket
handshake (`api/ws.ts`) carry no token because the same-origin requests
authenticate through the `HttpOnly` cookies. This assumes same-origin
SPA/API hosting — which the production static serving and the Vite dev
proxy both provide — while non-browser clients keep using the token pair in
login/refresh JSON responses with `Authorization: Bearer` headers.

Entity detail pages expose an "Activities" action that navigates to
`/{entity}/{id}/activities` (`EntityActivitiesView`, shared across `track`,
`album`, `artist`, `playlist`, and `library`). The feed reads
`GET /api/v1/{entity_type}/{entity_id}/activities` with `activity_type` /
`source_type` filters and keyset `cursor` pagination; liking or boosting an activity
calls `POST /api/v1/activities/{id}/like` / `/{id}/boost`, replying calls
`POST /api/v1/activities/{id}/reply`, and editing one calls
`PATCH /api/v1/activities/{id}` (shown to the activity owner and admins).
`ActivityCard` renders a Mastodon-style action bar — reply, boost and like
icons with their counters — for authenticated users when the activity's
`can_interact` flag allows it (the icons act as toggles — liking/boosting
a reacted activity retracts it via `DELETE /{id}/like` / `/{id}/boost`;
clicking the like/boost
count opens `ActivityActorsModal` listing the known interactors from
`GET /{id}/likes` / `/{id}/boosts`, and clicking the reply count expands
the known replies from `GET /{id}/replies` — local replies rendered as
nested `ActivityCard`s, remote ones as `ActivityRemoteReply` rows —
together with a `StatusComposer` wired to the reply endpoint). Card
content is never rendered as raw HTML — `ActivityCard` reduces it to safe
segments (text, line breaks, linkified mentions/hashtags/URLs) via
`utils/activityContent.parseActivityContent`; inline formatting elements
(`<strong>`, `<em>`, `<code>`, `<del>`, `<u>`, headings) are preserved as
`marks` on the segments and rendered through CSS classes rather than real
markup, and `<ul>`/`<ol>` items flatten to bullet/numbered lines with
indentation per nesting level. `ActivityCard` also renders
the activity's `attachments` (the AP `attachment` documents of the embedded
object): image media types inline, `Audio`/audio media types in an
`<audio>` player, everything else as a link. Reaction cards
(`like`/`announce`) are wrappers: `ActivityObjectEmbed` fetches the reacted
activity through a shared per-id cache (`utils/activityFetch.ts`) and
renders the full `ActivityCard` for `Note` objects or the compact item
card for `Audio` ones (falling back to an "unavailable" placeholder on
fetch failure), the card's timestamp/copy links point at the reaction's
`object_url`, and the Delete action retracts the caller's reaction
(`stores/activities.retractReaction`) rather than deleting the target.

`UserProfileView` (`/@{username}`) shows a Compose button to the profile
owner that opens `components/statuses/StatusComposer.vue` in a modal; the
share dialog's Fediverse tab reuses the same component for track
publication, and the default post format is configurable from
`/settings` (`profile.status_content_type`).

The home page (`HomeView`, `components/home/`) is a hybrid shelf + feed
layout that splits by audience and puts the two zones behind
`Music | Activity` `AppTabs` (shelves first) so the feed no longer trails
below the whole catalogue; both panels stay mounted (`v-show`) so tab
switches never refetch. Authenticated visitors get a greeting with
a `StatusComposer` modal, at most four "your music" shelves (Jump back in
from `/history/` deduplicated by track, Favorites, Your uploads, New on
this instance), and the scoped activity feed. Anonymous visitors get the
instance hero (name, description, sign-in/register CTAs, optional
`public_stats_enabled`-gated counts from `GET /api/v1/instance/stats`),
public-catalogue shelves (recently added albums/tracks, public libraries,
people), genre chips, and the public instance feed. Every shelf fetches its
own data, renders a skeleton while loading, offers inline retry on error,
and removes itself entirely when empty. The feed (`HomeFeed`, backed by
`stores/timeline.ts` and `GET /api/v1/timeline`) offers a scope switch on
its own row — `Mine | This instance | Federated` for signed-in users,
`This instance | Federated` for anonymous visitors — and a
`Posts | All activity` mode switch on a second row. `This instance` only
surfaces locally sourced activities; `Federated` adds activities received
from remote instances and webmentions. Signed-in users default to `Mine`
when they have own activity, and the last explicit choice persists in
`localStorage` (a stored `Mine` is ignored once logged out).

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

When the fallback serves the SPA shell for an object page (`/tracks/{id}`,
`/albums/{id}`, `/artists/{id}`, `/playlists/{id}`, `/libraries/{id}`,
`/genres/{name}`, `/tags/{name}` and `/@{username}` plus their sub-pages),
`api/semantic_meta.py` injects semantic `<head>` tags into it: OpenGraph
`og:title`/`og:description`/`og:url`/`og:type`/`og:site_name`/`og:image`
metadata for social-media preview cards, and `<link rel="tag">` elements for
the entity's hashtags. Ownership and related entities are expressed with the
OpenGraph music namespace and HTML authorship annotations: `music:musician`
links tracks and albums to their artist page, `music:album` links a track to
its album, `music:creator` links playlists/libraries to their owner, and
every uploadable entity also carries `<link rel="author">`, `name="author"`
and `fediverse:creator` tags pointing at the uploading user. Lookups go
through the regular ACL checks so private entities never leak metadata, and
any failure falls back to the unmodified shell.

Single-user mode is configured through the `single_user_username` admin
setting. When set, anonymous browser requests to `/` are answered with a
302 redirect to `/@{username}` by the always-mounted `profile_pages`
route, and the SPA router guard applies the same anonymous-only redirect
for client-side navigations; authenticated users keep the regular home
page and ActivityPub clients still get the SPA shell as before. Every
`/` response in this mode also advertises `rel="me"` links to the
single-user profile — both the `/@{username}` and `/users/{username}`
forms, injected into the SPA `<head>` and the `Link` header (the header
is the only channel on the redirect, which has no body) — so link
verification (e.g. on Mastodon) succeeds even when only the instance
base URL is referenced. The setting is exposed as
`single_user` on `/api/v1/instance` and `/api/v2/instance`.

The Vite build also copies the `swagger-ui-dist` bundle into
`songhive/static/swagger-ui/` and rewrites `swagger-initializer.js` to point
at the instance's own `/openapi.json`. FastAPI mounts those assets at
`/swagger-ui/` (with a redirect from `/swagger-ui`), so interactive API docs
are available on every deployment without a separate container.

---

## API Design

REST API under `/api/v1/`:

```
/api/v1/
├── auth/           # Login, register, token refresh, password reset, verify email, sessions
├── users/          # Public user profiles, directory, per-user activity feeds; authenticated profile updates
├── artists/        # Artist CRUD + search
├── albums/         # Album CRUD + search
├── tracks/         # Track CRUD + search
├── search/         # Lightweight aggregate multi-entity search (tracks, albums, artists,
│                   #   playlists, libraries, users, tags, genres) with normalized result
│                   #   items; ACL-filtered like the underlying list endpoints.
│                   #   A q starting with '#' is a hashtag lookup: the prefix is stripped
│                   #   and only the tags section is returned, sorted by item_count
│                   #   (a bare '#' lists the most used tags). With remote_users=1 the
│                   #   users section also matches remote actors cached by pubby
│                   #   (federation_followers + federation_actor_cache), returning
│                   #   'user@domain' handles for mention completion
├── files/          # Generic file upload/list/download (StoredFile)
├── libraries/      # Library management + add/remove tracks/albums/artists
├── playlists/      # Playlist CRUD + add/remove/reorder tracks/albums/artists + list tracks
├── favorites/      # Favorites/bookmarks
├── history/        # Listening history
├── radios/         # Dynamic radio generation
├── timeline/       # Cross-entity activity feed: scope=mine|instance (mine
│                   #   requires auth; anonymous defaults to instance),
│                   #   mode=posts|all with include_boosts/include_replies/
│                   #   source_type filters, keyset cursor pagination.
│                   #   No `following` scope — Songhive only receives follows
├── instance/       # Public instance metadata (Mastodon-compatible);
│                   #   `single_user` reports the single_user_username setting,
│                   #   /instance/stats exposes visibility-filtered counts when
│                   #   the public_stats_enabled admin setting is on (404 otherwise)
├── statuses/       # Standalone status posts (user-entity `create` activities with
│                   #   content type, language, file/track attachments, and mentions)
├── shares/         # Share grants (owner → specific user); /shares/mine lists the
│                   #   grants + tokens created by the current user (revoked
│                   #   tokens only with ?include_revoked=true)
├── share-urls/     # Share URL tokens (revocable short links)
├── share/{token}   # Public short-URL resolver
├── reports/        # Content moderation reports (submit)
├── admin/          # Admin: settings, stats, user management, report review
└── stream/{id}     # Audio streaming (Tornado handler, bypasses FastAPI)
```

**Federation endpoints** (mounted by pubby when federation is enabled):

```
/users/{username}               # Per-user ActivityPub actor document (AP clients) or browser redirect to /@{username}
/users/{username}/objects/{id}  # Dereferenceable ActivityPub objects (Audio, Note, Tombstone, stored payloads)
/users/{username}/quote_authorizations/{id}  # FEP-044f QuoteAuthorization documents issued for the user actor
/@{username}                    # Mastodon-style profile: AP actor for AP clients, SPA shell for browsers with rel="me" links + OpenGraph tags
/tracks/{id}                    # Track page: Audio object for AP clients, SPA + rel=alternate hints + OpenGraph tags for browsers
/.well-known/webfinger          # WebFinger discovery
/.well-known/nodeinfo           # NodeInfo discovery document (pubby)
/nodeinfo/2.{0,1}[.json]        # NodeInfo document (Songhive): pubby usage stats plus
                                # metadata.nodeName/nodeDescription, metadata.maintainer
                                # (configured contact person) and metadata.staffAccounts
                                # (actor URLs of active admins)
/ap/actor                       # Instance-level Application actor
/ap/inbox                       # Shared inbox (Songhive route; queues process_incoming)
/api/v1/*/                      # Mastodon-compatible API (pubby adapter), except /api/v1/instance which is provided by Songhive and always available
```

---

## Deployment

Docker Compose (`docker-compose.yml`) provides a reference deployment:

- `songhive` — application container (Tornado server)
- `celery` — Celery worker container (same image, different entrypoint)
- `postgres` — PostgreSQL database
- `redis` — Redis (broker + cache + sessions)
- `nginx` — Reverse proxy (`docker/nginx.conf`). It proxies everything to the
application, so the REST API, federation endpoints, frontend assets and the
SPA shell are all served by FastAPI (`api/app.py` sets `router.default` to a
handler that serves `songhive/static/` files directly and falls back to
`index.html`). Serving the SPA through the backend lets it inject `rel="me"`
links, `rel="alternate"` ActivityPub hints and OpenGraph/`rel="tag"` metadata
into object pages (`api/routes/profile_pages.py` and `api/semantic_meta.py`),
and perform the ActivityPub/browser content negotiation for routes that
double as dereferenceable AP objects. Only two paths get special treatment:
`/ws/` needs the WebSocket upgrade headers and `/api/v1/stream/` disables
response buffering for real-time audio delivery.

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

The master `songhive.service` unit starts four dependent units:

- `songhive-server.service` — main web server (`songhive`)
- `songhive-celery.service` — Celery worker and scheduler
- `songhive-watch-extlib.service` — external-library watchdog
- `songhive-stream-worker.service` — audio stream worker for server-side outputs (`songhive stream-worker`)
