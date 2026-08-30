# Songhive

[![Build Status](https://ci-cd.platypush.tech/api/badges/blacklight/songhive/status.svg)](https://ci-cd.platypush.tech/blacklight/songhive)
[![Coverage Badge](https://app.codacy.com/project/badge/Coverage/f8740f0a9f7e40f0a134441bd5570690)](https://app.codacy.com/gh/blacklight/songhive/dashboard?utm_source=gh&utm_medium=referral&utm_content=&utm_campaign=Badge_coverage)
[![Codacy Badge](https://app.codacy.com/project/badge/Grade/f8740f0a9f7e40f0a134441bd5570690)](https://app.codacy.com/gh/blacklight/songhive/dashboard)
[![CodeFactor](https://www.codefactor.io/repository/github/blacklight/songhive/badge)](https://www.codefactor.io/repository/github/blacklight/songhive)
[![Github stars](https://img.shields.io/github/stars/blacklight/songhive?style=flat&logo=Github)](https://github.com/blacklight/songhive)
[![Github forks](https://img.shields.io/github/forks/blacklight/songhive?style=flat&logo=Github)](https://github.com/blacklight/songhive)
[![Last Commit](https://img.shields.io/github/last-commit/BlackLight/songhive.svg)](https://git.platypush.tech/songhive/songhive/commits/branch/main)
[![License](https://img.shields.io/github/license/blacklight/songhive.svg)](https://git.platypush.tech/blacklight/songhive/src/branch/main/LICENSE)

<!-- toc -->

- [Overview](#overview)
- [Features](#features)
- [Architecture](#architecture)
- [Quickstart](#quickstart)
  * [With Docker (recommended)](#with-docker-recommended)
  * [With pip](#with-pip)
    + [Latest stable package](#latest-stable-package)
    + [Development from source](#development-from-source)
    + [Initialization](#initialization)
- [Development](#development)
  * [Frontend](#frontend)
- [API](#api)
- [License](#license)

<!-- tocstop -->

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

## Quickstart

Songhive can be run either as a complete Docker stack or installed locally with
`pip`.

### With Docker (recommended)

The Docker Compose setup builds the frontend and backend images, starts
PostgreSQL and Redis, and wires everything together behind an Nginx reverse
proxy. The `songhive`, `worker`, `postgres` and `redis` services all run as the
same non-root UID/GID as the host user, so the files in `./volumes` are owned by
you and are easy to access from the host.

Prerequisites:

- Docker and Docker Compose
- git

```bash
# Clone the repository
git clone https://git.fabiomanganiello.com/songhive
# Or from GitHub: git clone https://github.com/blacklight/songhive
cd songhive

# Set the UID/GID to match the host user (the same value is used by all
# rootless services and by the setup step that fixes volume permissions).
export PUID=$(id -u)
export PGID=$(id -g)

# Build and start all services
docker compose up -d --build

# Create the first admin user
docker compose exec songhive songhive admin create-user \
    --username admin \
    --email admin@example.com \
    --password secret \
    --admin
```

Then open:

- Web UI: http://localhost/
- Swagger UI: http://localhost/swagger-ui/
- OpenAPI spec: http://localhost/openapi.json

Stop the stack with `docker compose down`.

The Docker entrypoint initializes the database tables and persists a JWT signing
secret in `volumes/data/secret_key`, so no manual database setup is required. A
one-off `setup` container creates and `chown`s the `./volumes` directories to
`$PUID:$PGID` before the main services start. If you prefer to prepare the
volumes yourself, you can also run `PUID=$(id -u) PGID=$(id -g) ./scripts/setup-volumes.sh`.

### With pip

This path is useful for local development or running on an existing Python host.
A published package is also available on PyPI and ships the built web UI, so the
frontend does not need to be built manually when installing from PyPI.

Prerequisites:

- Python >= 3.10
- PostgreSQL
- Redis
- ffmpeg
- Node.js and npm (only required to build the web UI from source)

#### Latest stable package

```bash
# Install from PyPI
pip install songhive
```

#### Development from source

Or, clone the repository and install in editable mode for development

```bash
git clone https://git.fabiomanganiello.com/songhive.git
cd songhive

# Optional: create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

pip install -e .

# Optional: build the web UI (outputs to songhive/static/)
cd frontend
npm install
npm run build
cd ..
```

#### Initialization

These steps are only required for a local installation (not Docker). Adjust the
paths and credentials as needed.

Create a database and user in PostgreSQL (adjust to match your setup):

```bash
sudo -u postgres psql <<'SQL'
CREATE USER songhive WITH PASSWORD 'songhive';
CREATE DATABASE songhive OWNER songhive;
SQL
```

Copy the example configuration file and edit it:

```bash
cp config.toml.example config.toml
```

Set at least the following values in `config.toml`:

```toml
[auth]
secret_key = "..."  # Generate with: python -c "import secrets; print(secrets.token_urlsafe(64))"

[storage]
local_path = "/path/to/writable/media"  # e.g. ./data/media

[server]
cors_origins = ["*"]  # Replace with your frontend origin(s) in production

[federation]
enabled = false  # Set a real instance_domain to enable federation
```

Create the storage directory:

```bash
mkdir -p /path/to/writable/media
```

Initialize the database tables (one-time):

```bash
songhive admin init-db
```

Start the Celery worker in a second terminal:

```bash
celery -A songhive.tasks worker -B -l info
```

Start the Songhive server:

```bash
songhive
```

Create the first admin user in another terminal:

```bash
songhive admin create-user \
    --username admin \
    --email admin@example.com \
    --password secret \
    --admin
```

If the web UI has been built, it is available at http://localhost:8000/; the
interactive API docs (Swagger) are at http://localhost:8000/docs, and the REST
API is served at `/api/v1/`.

For UI development, run `npm run dev` from the `frontend/` directory instead of
`npm run build`. If you use the Vite dev server (http://localhost:5173 by
default), add it to `server.cors_origins`.

## Development

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
| `/api/v1/auth/api-tokens/` | API token management (create, list, revoke) |
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
