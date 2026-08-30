# CHANGELOG

All notable changes to this project will be documented in this file.

## Unreleased

### Changed

- Clarify the example configuration's auth secret and SQLite database option. ([`53ff5be`](https://git.platypush.tech/blacklight/songhive/commit/53ff5bee36e0034ebaedcdff50353e7c7df51826))

### Fixed

- `migrations`: Serialize concurrent `ensure_migrated` runs to prevent
  duplicate-key crashes on fresh Docker Compose deployments. ([`877e1f6`](https://git.platypush.tech/blacklight/songhive/commit/877e1f6d5ccde51fd0d57c256064860d255dc811))
- `db`: Reset the async engine and session factory after Celery task event loops
  to prevent asyncpg loop-binding errors during track uploads and other
  background tasks. ([`ab53219`](https://git.platypush.tech/blacklight/songhive/commit/ab53219f93c4b80b721c4135a2cf0127bc1ec2cb))

## 0.0.10

### Changed

- `docker`: Switch Docker Compose to the published `quay.io/blacklight/songhive`
  image and add a `docker/bootstrap.sh` script to fetch compose files and sample
  config. ([`3cd029d`](https://git.platypush.tech/blacklight/songhive/commit/3cd029d3b71f8d52a7720d3f5b5ba8998779ccfd))
- Restructure the README install and run instructions, adding Docker bootstrap,
  local build, expanded pip setup, Celery and admin user steps, and updated
  nginx reverse proxy notes. ([`b397aa5`](https://git.platypush.tech/blacklight/songhive/commit/b397aa553645c8a8bda05dd0e6775ec4df417b27))
- `docker`: Speed up multi-arch image builds with `npm ci` and
  `package-lock.json`, split Python dependency layers, and `buildx` registry
  caching. ([`23d3e05`](https://git.platypush.tech/blacklight/songhive/commit/23d3e053c83feb284636439211f595f36be6ce07))
- `docker`: Replace `apt-get install ffmpeg` with the static
  `mwader/static-ffmpeg` binary to remove the large Debian dependency tree and
  reduce image size. ([`25b7030`](https://git.platypush.tech/blacklight/songhive/commit/25b7030961d2670dadd97ff45bfad82f9556eb66))
- Document SQLite as a database option, with a note that it is not recommended
  for large installations. ([`48ea6b1`](https://git.platypush.tech/blacklight/songhive/commit/48ea6b17aa29e70769738f482a436b28ea795acf))
- `drone`: Drop the multi-architecture `buildx` platform flag to avoid slow
  ARM64 QEMU emulation. ([`6e2790b`](https://git.platypush.tech/blacklight/songhive/commit/6e2790b2cc1f5f47aaf237676b4b01e7fb3aa8de))

### Fixed

- Correct the `config.toml.example` download URL in the Docker bootstrap script. ([`9649cf3`](https://git.platypush.tech/blacklight/songhive/commit/9649cf3007601fd541982b407ea59864ff3a27a2))
- `db`: Dispose the engine after the temporary settings overlay so request loops
  create fresh asyncpg connections on their own event loop. ([`5caf601`](https://git.platypush.tech/blacklight/songhive/commit/5caf60162d90f202532f8c9a3995e7e2a6646046))
- `bootstrap`: Skip downloading `config.toml.example` when `config.toml` already
  exists. ([`6a1c81c`](https://git.platypush.tech/blacklight/songhive/commit/6a1c81ce8767bf38748be587bdcf4db0699f7882))

## 0.0.9

### Fixes

- ci: Updated Python version for Docker image to 3.14.
- ci: Removed armv7 Docker image build process (psycopg2-binary is not supported on armv7).

## 0.0.8

### Fixes

- ci: Fixed Docker image release process.

## 0.0.2

Initial release.
