# Songhive

[![Build Status](https://ci-cd.platypush.tech/api/badges/blacklight/songhive/status.svg)](https://ci-cd.platypush.tech/blacklight/songhive)
[![Codacy Badge](https://app.codacy.com/project/badge/Grade/f8740f0a9f7e40f0a134441bd5570690)](https://app.codacy.com/gh/blacklight/songhive/dashboard)
[![CodeFactor](https://www.codefactor.io/repository/github/blacklight/songhive/badge)](https://www.codefactor.io/repository/github/blacklight/songhive)
[![Github stars](https://img.shields.io/github/stars/blacklight/songhive?style=flat&logo=Github)](https://github.com/blacklight/songhive)
[![Github forks](https://img.shields.io/github/forks/blacklight/songhive?style=flat&logo=Github)](https://github.com/blacklight/songhive)
[![Last Commit](https://img.shields.io/github/last-commit/BlackLight/songhive.svg)](https://git.platypush.tech/songhive/songhive/commits/branch/main)
[![License](https://img.shields.io/github/license/blacklight/songhive.svg)](https://git.platypush.tech/blacklight/songhive/src/branch/main/LICENSE)

A federated and self-hosted music sharing service, built with ActivityPub
federation support.

## Overview

Songhive is a music streaming platform similar to
[Funkwhale](https://funkwhale.audio), with a focus on better federation. It
allows users to upload, organize, and stream their music library while
federating with other instances (including Mastodon) via ActivityPub.

## Features

- **Music Library**: Upload and organize artists, albums, and tracks
- **Streaming**: Audio streaming with on-the-fly transcoding (MP3, OGG, FLAC, AAC, Opus)
- **Federation**: ActivityPub support via [pubby](https://github.com/blacklight/pubby) — federate with Mastodon and other AP-compatible services
- **Playlists & Radios**: Create playlists and dynamic radio stations
- **Multi-user**: User registration, profiles, and admin management
- **OAuth2 Provider**: Third-party app authorization
- **Subsonic API**: Compatibility layer for Subsonic clients
- **Flexible Storage**: Local filesystem or S3-compatible object storage

## Architecture

- **Backend**: FastAPI (REST API) + Tornado (WebSocket, streaming, process server)
- **Models**: Pydantic (validation) + SQLAlchemy (async ORM)
- **Tasks**: Celery + Redis (background import, transcoding, federation delivery)
- **Frontend**: Vue.js 3 + TypeScript + Pinia

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for detailed architecture documentation.

## Quick Start

### Requirements

- Python >= 3.10
- PostgreSQL
- Redis
- ffmpeg (for transcoding)

### Installation

```bash
# Clone the repository
git clone https://git.fabiomanganiello.com/songhive.git
# Or from GitHub mirror:
# git clone https://github.com/blacklight/songhive.git
cd songhive

# Install Python dependencies
pip install -e .

# Install dev dependencies
pip install -r requirements-dev.txt
```

### Configuration

Configuration is loaded from (in priority order):

1. Environment variables (prefixed `SONGHIVE_`)
2. CLI arguments
3. `config.toml` file

Create a `config.toml`:

```toml
[server]
host = "0.0.0.0"
port = 8000
cors_origins = ["*"]

[database]
url = "postgresql+asyncpg://songhive:songhive@localhost:5432/songhive"

[redis]
url = "redis://localhost:6379/0"

[federation]
enabled = true
instance_domain = "music.example.com"
instance_name = "My Music Server"

[auth]
registration_mode = "open"
require_email_verification = false
secret_key = "your-secret-key-here"
access_token_expiry_minutes = 15
refresh_token_expiry_days = 30

[email]
# Uncomment and configure to enable outbound email.
# smtp_host = "smtp.example.com"
# smtp_port = 587
# smtp_username = ""
# smtp_password = ""
# smtp_tls = true
# from_address = "songhive@example.com"

[storage]
backend = "local"
local_path = "/var/lib/songhive/media"
```

Or see the [full configuration example](./config.toml.example).

CORS origins are configured with `server.cors_origins`. In production, replace the
`["*"]` wildcard with a list of trusted frontend origins, set the
`SONGHIVE_SERVER__CORS_ORIGINS` environment variable, or pass `--cors-origins` on
the command line.

### Running

```bash
# Start the server
songhive

# Or with CLI options
songhive --port 9000 --debug

# Admin commands
songhive admin create-user --username admin --email admin@example.com --password secret --admin
```

### Development

```bash
# Run tests
python -m pytest

# Run linting
python -m flake8 songhive tests

# Format code
python -m black .

# Start Celery worker
celery -A songhive.tasks worker -l info
```

### Frontend

```bash
cd frontend
npm install
npm run dev     # Development server
npm run build   # Production build (outputs to songhive/static/)
```

## API

REST API available at `/api/v1/`:

| Endpoint | Description |
|----------|-------------|
| `/api/v1/auth/` | Authentication (login, register, refresh) |
| `/api/v1/users/` | User profiles |
| `/api/v1/artists/` | Artists |
| `/api/v1/albums/` | Albums |
| `/api/v1/tracks/` | Tracks |
| `/api/v1/playlists/` | Playlists |
| `/api/v1/libraries/` | User libraries |
| `/api/v1/favorites/` | Favorites |
| `/api/v1/history/` | Listening history |
| `/api/v1/radios/` | Dynamic radios |
| `/api/v1/stream/{id}` | Audio streaming |
| `/api/v1/admin/` | Admin endpoints |

WebSocket: `/ws/events` (real-time notifications)

Federation: `/.well-known/webfinger`, `/ap/actor`, `/ap/inbox`, `/ap/outbox`

## License

AGPL-3.0
