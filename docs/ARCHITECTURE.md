# Songhive Architecture

## System Overview

Songhive is a federated and self-hosted music sharing service. It uses
ActivityPub for federation (via [pubby](https://github.com/blacklight/pubby))
and aims to be compatible with the Mastodon API and Subsonic API where
applicable.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              External Services                              │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐ │
│  │ MusicBrainz │  │ ListenBrainz│  │  Last.fm    │  │ Federated Instances │ │
│  └─────────────┘  └─────────────┘  └─────────────┘  └─────────────────────┘ │
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
│    Frontend (Vue.js)    │ │  Tornado Server │ │   Static/Media Files        │
│                         │ │  (FastAPI ASGI) │ │   (Local/S3)                │
└─────────────────────────┘ └─────────────────┘ └─────────────────────────────┘
                                      │
                    ┌─────────────────┼─────────────────┐
                    ▼                 ▼                 ▼
            ┌─────────────┐   ┌─────────────┐   ┌─────────────┐
            │  PostgreSQL │   │    Redis    │   │   Celery    │
            │  (Database) │   │   (Cache/   │   │  (Workers)  │
            │             │   │   Broker)   │   │             │
            └─────────────┘   └─────────────┘   └─────────────┘
```

---

## Backend Stack

| Component     | Technology                         |
|---------------|------------------------------------|
| API Framework | FastAPI (ASGI)                     |
| Server        | Tornado (wrapping FastAPI via ASGI)|
| WebSockets    | Tornado WebSocket handlers         |
| Data Models   | Pydantic (validation/serialization)|
| ORM           | SQLAlchemy (async)                 |
| Task Queue    | Celery + Redis                     |
| Cache         | Redis                              |
| Federation    | pubby (ActivityPub library)        |
| Auth          | OAuth2 (authlib)                   |
| Transcoding   | ffmpeg (system dependency)         |

---

## Application Structure

```
songhive/
├── __init__.py
├── __main__.py
├── app.py                  # Application entry point & Tornado+FastAPI bootstrap
├── version.py
├── config/                 # Configuration management
│   ├── __init__.py
│   ├── schema.py           # Pydantic settings model
│   └── loader.py           # TOML + env + CLI loading
├── api/                    # FastAPI application & routes
│   ├── __init__.py
│   ├── app.py              # FastAPI app factory
│   ├── deps.py             # Dependency injection helpers
│   ├── routes/             # Route modules
│   │   ├── __init__.py
│   │   ├── auth.py
│   │   ├── users.py
│   │   ├── artists.py
│   │   ├── albums.py
│   │   ├── tracks.py
│   │   ├── playlists.py
│   │   ├── libraries.py
│   │   ├── favorites.py
│   │   ├── history.py
│   │   ├── radios.py
│   │   ├── admin.py
│   │   └── subsonic.py
│   └── middleware/
│       ├── __init__.py
│       └── auth.py         # JWT/OAuth2 middleware
├── models/                 # SQLAlchemy + Pydantic models
│   ├── __init__.py
│   ├── base.py             # SQLAlchemy base, session management
│   ├── user.py
│   ├── artist.py
│   ├── album.py
│   ├── track.py
│   ├── upload.py
│   ├── library.py
│   ├── playlist.py
│   ├── favorite.py
│   ├── history.py
│   └── radio.py
├── services/               # Business logic layer
│   ├── __init__.py
│   ├── auth.py
│   ├── music.py
│   ├── streaming.py
│   ├── import_.py
│   └── metadata.py
├── federation/             # ActivityPub integration (via pubby)
│   ├── __init__.py
│   ├── actors.py           # Actor management
│   ├── activities.py       # Activity creation & processing
│   └── serializers.py      # Audio→ActivityPub object mappers
├── users/                  # User management
│   ├── __init__.py
│   ├── manager.py
│   └── oauth.py            # OAuth2 provider setup
├── music/                  # Music domain logic
│   ├── __init__.py
│   ├── importer.py
│   └── metadata.py         # Tag reading (mutagen)
├── streaming/              # Audio streaming & transcoding
│   ├── __init__.py
│   ├── handler.py          # Tornado streaming handler
│   └── transcoder.py       # ffmpeg wrapper
├── storage/                # Media storage backends
│   ├── __init__.py
│   ├── base.py             # Abstract storage interface
│   ├── local.py
│   └── s3.py
├── tasks/                  # Celery task definitions
│   ├── __init__.py
│   ├── celery.py           # Celery app factory
│   ├── import_.py
│   ├── federation.py
│   └── transcoding.py
├── ws/                     # WebSocket handlers (Tornado)
│   ├── __init__.py
│   └── events.py
└── cli/                    # CLI commands (argparse)
    ├── __init__.py
    └── admin.py            # Admin commands (create user, etc.)
```

---

## Server Architecture: Tornado + FastAPI

The server uses Tornado as the top-level HTTP server, with the FastAPI ASGI
application mounted via Tornado's `ASGIContainer`. This gives us:

- **Tornado**: Native WebSocket support, streaming handlers, process forking
- **FastAPI**: Modern REST API with automatic OpenAPI docs, dependency injection,
  Pydantic validation

```python
# Simplified bootstrap pattern
from tornado.web import Application, FallbackHandler
from tornado.httpserver import HTTPServer
from tornado.routing import RuleRouter
from tornado_asgi import ASGIContainer

from songhive.api.app import create_app
from songhive.ws.events import EventWebSocket
from songhive.streaming.handler import StreamHandler

fastapi_app = create_app()
container = ASGIContainer(fastapi_app)

tornado_app = Application([
    (r"/ws/events", EventWebSocket),
    (r"/api/v1/stream/(?P<track_id>[^/]+)", StreamHandler),
    (r".*", FallbackHandler, {"fallback": container}),
])

server = HTTPServer(tornado_app)
server.listen(8000)
```

---

## Configuration

Configuration is loaded with the following priority (highest first):

1. Environment variables (prefixed `SONGHIVE_`)
2. CLI arguments (`--port`, `--db-url`, etc.)
3. `config.toml` file (searched in `./config.toml`, `~/.config/songhive/config.toml`)

The configuration schema is defined as a Pydantic `BaseSettings` model.

Server settings include `cors_origins` to control the allowed CORS origins for the
API (e.g. the frontend URL). It can be set in `config.toml`, via the
`SONGHIVE_SERVER__CORS_ORIGINS` environment variable, or with the `--cors-origins`
CLI argument.

---

## Data Model (Core Entities)

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   Artist    │────▶│    Album    │────▶│    Track    │
└─────────────┘     └─────────────┘     └─────────────┘
                                               │
                                               ▼
                                        ┌─────────────┐
                                        │   Upload    │
                                        │ (file/S3)   │
                                        └─────────────┘

┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│    User     │────▶│  Library    │────▶│  Playlist   │
└─────────────┘     └─────────────┘     └─────────────┘
```

---

## Federation

Federation is powered by [pubby](https://github.com/blacklight/pubby) using
its FastAPI adapter (`pubby.server.adapters.fastapi.bind_activitypub`).

- Each user has an ActivityPub actor
- Uploaded tracks are published as `Audio` objects (ActivityPub `Create`)
- Each public lifecycle has its own ActivityPub object id (`Track.federation_object_id`); a new id is generated on every public transition and used for `Delete(Tombstone)` when the track is made non-public or deleted
- This prevents a previous `Tombstone` at the same URL from blocking re-publication of the same track
- Following/unfollowing is handled via standard AP activities
- Per-actor follower isolation is delegated to pubby's native `target_actor_id` support
- Media content renders on Mastodon as a post with an embedded audio link

When federation is enabled, each user is reachable at:

- `https://{instance_domain}/users/{username}` — the canonical ActivityPub
  `Person` actor document.
- `https://{instance_domain}/@{username}` — a Mastodon-like alias that returns
  the actor document for ActivityPub clients and redirects browsers to the local
  profile.
- `https://{instance_domain}/.well-known/webfinger?resource=acct:{username}@{instance_domain}`
  — WebFinger discovery returning the actor URL.

Existing users created before federation was configured can be back-filled with
actor URLs and RSA keypairs by running the admin CLI command
`python -m songhive.cli.admin provision-federation-keys`.

---

## Authentication & Authorization

- **Local auth**: Username/password with bcrypt hashing
- **JWT tokens**: For API access
- **OAuth2 provider**: For third-party app authorization (authlib)
- **Subsonic token**: For Subsonic API compatibility
- **Admin role**: Elevated permissions for instance management

---

## Streaming & Transcoding

- Tornado streaming handler serves audio data
- Supports MP3, OGG, FLAC, AAC formats natively
- Uses ffmpeg for on-the-fly transcoding when the client requests a different
  format than the stored file
- Range requests supported for seeking

---

## Storage Backends

| Backend | Description                          |
|---------|--------------------------------------|
| Local   | Files stored on local filesystem     |
| S3      | S3-compatible object storage         |

Storage backend is selected via configuration. Both implement the same abstract
interface (`StorageBackend`).

---

## Task Queue (Celery)

Background tasks handled by Celery workers:

- **Import**: File upload processing, metadata extraction
- **Federation**: Activity delivery, inbox processing
- **Transcoding**: Pre-transcoding to common formats

Redis is used as both the Celery broker and result backend.

---

## Frontend

Vue.js 3 + TypeScript SPA, bundled separately. The backend serves the built
frontend files as static assets. Distribution options:

- CI pipeline builds and commits dist to the repository
- Separate repo imported as a submodule
- S3 bucket for CDN hosting

---

## API Design

REST API under `/api/v1/`:

```
/api/v1/
├── auth/           # Login, register, token refresh
├── users/          # User profiles
├── artists/        # Artist CRUD + search
├── albums/         # Album CRUD + search
├── tracks/         # Track CRUD + search
├── uploads/        # File upload management
├── libraries/      # Library management
├── playlists/      # Playlist CRUD
├── favorites/      # Favorites/bookmarks
├── history/        # Listening history
├── radios/         # Dynamic radio generation
├── stream/{id}     # Audio streaming (handled by Tornado)
└── admin/          # Admin endpoints
```

Compatibility layers:

- `/api/subsonic/` — Subsonic API compatibility
- ActivityPub endpoints via pubby (`.well-known/webfinger`, actor, inbox, outbox)
