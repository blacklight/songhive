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
- [Installation](#installation)
  * [Docker](#docker)
    + [Latest image](#latest-image)
    + [From a local checkout](#from-a-local-checkout)
  * [pip](#pip)
    + [Latest stable package](#latest-stable-package)
    + [From a local checkout](#from-a-local-checkout-1)
  * [nginx setup](#nginx-setup)
- [Configuration](#configuration)
  * [Getting the default configuration](#getting-the-default-configuration)
  * [Base configuration](#base-configuration)
  * [From environment variables](#from-environment-variables)
- [Running the service](#running-the-service)
  * [Docker installation](#docker-installation)
  * [pip installation](#pip-installation)
    + [Celery](#celery)
    + [Local library watchdog](#local-library-watchdog)
  * [systemd service](#systemd-service)
  * [Creating the admin user](#creating-the-admin-user)
    + [Docker installation](#docker-installation-1)
    + [pip installation](#pip-installation-1)
- [Testing the installation](#testing-the-installation)
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
- **Metadata Enrichment**: Fetch metadata from external services
- **Federation**: ActivityPub support via [pubby](https://github.com/blacklight/pubby) — federate with Mastodon and other AP-compatible services
- **Playlists & Radios**: Create playlists and dynamic radio stations
- **Multi-user**: User registration, profiles, and admin management
- **OAuth2 Provider**: Third-party app authorization
- **Subsonic API**: Compatibility layer for Subsonic clients
- **Flexible Storage**: Local filesystem or S3-compatible object storage
- **External Libraries**: Attach external music storage (e.g. cloud adapters) to
  Songhive libraries; index, stream, and write metadata back to the provider.
  See [docs/ARCHITECTURE.md#external-libraries](docs/ARCHITECTURE.md#external-libraries).

## Architecture

- **Backend**: FastAPI (REST API) + Tornado (WebSocket, streaming, process server)
- **Models**: Pydantic (validation) + SQLAlchemy (async ORM)
- **Tasks**: Celery + Redis (background import, transcoding, federation delivery)
- **Frontend**: Vue.js 3 + TypeScript + Pinia

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for detailed architecture documentation.

## Installation

Songhive can be run either as a complete Docker stack or installed locally with
`pip`.

### Docker

The Docker Compose setup builds the frontend and backend images, starts
PostgreSQL and Redis, and wires everything together behind an Nginx reverse
proxy. The `songhive`, `worker`, `postgres` and `redis` services all run as the
same non-root UID/GID as the host user, so the files in `./volumes` are owned by
you and are easy to access from the host.

#### Latest image

```bash
# Run the docker-compose bootstrap script
curl -fsSL https://git.fabiomanganiello.com/songhive/raw/branch/main/docker/bootstrap.sh | sh
```

#### From a local checkout

```bash
# Clone the repository
git clone https://git.fabiomanganiello.com/songhive
# Or from GitHub: git clone https://github.com/blacklight/songhive
cd songhive

# Set the UID/GID to match the host user (the same value is used by all
# rootless services and by the setup step that fixes volume permissions).
export PUID=$(id -u)
export PGID=$(id -g)

# Build the images
docker compose build
```

### pip

This path is useful for local development or running on an existing Python host.
A published package is also available on PyPI and ships the built web UI, so the
frontend does not need to be built manually when installing from PyPI.

Prerequisites:

- Python >= 3.10
- PostgreSQL (a SQLite database will also work, but it's not recommended for
  large installations)
- Redis/Valkey
- ffmpeg
- Node.js and npm (for the frontend)

#### Latest stable package

```bash
# Install from PyPI
pip install songhive
```

#### From a local checkout

Or, clone the repository and install in editable mode for development

```bash
git clone https://git.fabiomanganiello.com/songhive
# Or from GitHub: git clone https://github.com/blacklight/songhive
cd songhive

# Optional: create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

pip install -e .

# Build the web UI (outputs to songhive/static/)
cd frontend
npm install
npm run build
cd ..
```

### nginx setup

If you are planning to serve Songhive behind a reverse proxy, you can reuse the
[`nginx.conf`](./docker/nginx.conf) used by the Docker setup.

## Configuration

### Getting the default configuration

- If you installed Songhive through the docker-compose bootstrap script, then
  `config.toml` should be already downloaded under the same folder as
  `docker-compose.yml`.
- If you built Songhive from a local checkout, then copy the [example
  configuration file](./config.toml.example):

  ```bash
  cp config.toml.example config.toml
  ```
- Otherwise, download the latest `config.toml`:

  ```bash
  wget https://git.fabiomanganiello.com/songhive/raw/branch/main/config.toml.example
  ```

The application looks for `config.toml` in this order: the path given with
`--config` or the `SONGHIVE_CONFIG` environment variable, then `./config.toml`,
then `$XDG_CONFIG_HOME/songhive/config.toml` (or `~/.config/songhive/config.toml`),
and finally `/etc/songhive/config.toml`.

### Base configuration

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
# instance_domain = "music.example.com"
```

### From environment variables

All the `config.toml` configuration entries can be overridden via environment
variables.

For example:

```toml
[database]
url = "postgresql+asyncpg://songhive:songhive@localhost:5432/songhive"
```

becomes:

```bash
SONGHIVE_DATABASE__URL="postgresql+asyncpg://songhive:songhive@localhost:5432/songhive"
```

## Running the service

### Docker installation

```bash
cd /path/to/your/songhive/installation
docker compose up -d
```

Then take down the stack with:

```bash
docker compose down
```

### pip installation

```bash
SONGHIVE_CONFIG="/path/to/your/songhive/installation/config.toml"
songhive -c "$SONGHIVE_CONFIG"
```

#### Celery

This is only required in a non-Docker setup. The Docker stack already runs a
separate container for the Celery workers.

Start the Celery worker in a second terminal:

```bash
celery -A songhive.tasks worker -B -l info
```

#### Local library watchdog

If you are using the built-in `local` external-library provider, start the
filesystem watcher in another terminal or under a supervisor such as systemd:

```bash
songhive watch-external-libraries
```

The Docker stack runs this as a separate `watcher` container. The watcher is kept
as a standalone process rather than a child of the web server so that a single
host has exactly one watchdog, even when the web server is scaled to multiple
workers.

### systemd service

Songhive ships with systemd unit files under [`config/systemd/`](./config/systemd/)
and an [`install.sh`](./install.sh) script that sets up a virtual environment,
copies the example config, installs the units, and creates the required
directories.

The master `songhive.service` unit pulls in three units:

- `songhive-server.service` — the main web server
- `songhive-celery.service` — the Celery worker and scheduler
- `songhive-watch-extlib.service` — the external-library watchdog

Run the installer as **root** for a system-wide service:

```bash
sudo ./install.sh
```

This creates `/opt/songhive` (the virtual environment), `/etc/songhive`,
`/var/lib/songhive`, `/var/cache/songhive`, and `/var/log/songhive`, installs
the units to `/etc/systemd/system/`, and reminds you to copy
`/etc/songhive/config.toml.example` to `/etc/songhive/config.toml` and edit it.
Then start and enable the service:

```bash
sudo systemctl start songhive.service
sudo systemctl enable songhive.service
```

Run the installer as a **normal user** for a user service:

```bash
./install.sh
```

This creates a virtual environment under `~/.local/share/virtualenvs/songhive`,
copies the example config to `~/.config/songhive/`, creates
`~/.local/share/songhive`, `~/.cache/songhive`, and `~/.local/state/songhive`,
and installs the units to `~/.config/systemd/user/`. Copy
`~/.config/songhive/config.toml.example` to
`~/.config/songhive/config.toml`, edit it, then start the user service:

```bash
systemctl --user start songhive.service
systemctl --user enable songhive.service
```

### Creating the admin user

#### Docker installation

```bash
cd /path/to/your/songhive/installation
docker compose exec songhive songhive admin create-user \
    --username admin \
    --email admin@example.com \
    --password secret \
    --admin
```

#### pip installation

```bash
SONGHIVE_CONFIG="/path/to/your/songhive/installation/config.toml"
songhive -c "$SONGHIVE_CONFIG" admin create-user \
    --username admin \
    --email admin@example.com \
    --password secret \
    --admin
```

## Testing the installation

Open:

- **Web UI**: http://localhost:8000/
- **Swagger UI** (only for the Docker setup): http://localhost:8000/swagger-ui/
- **OpenAPI spec**: http://localhost:8000/openapi.json

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
