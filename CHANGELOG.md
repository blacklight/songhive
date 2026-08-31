# CHANGELOG

All notable changes to this project will be documented in this file.

## Unreleased

### Added

- Add playlist track reorder endpoint ([`b73c52c`](https://git.platypush.tech/blacklight/songhive/commit/b73c52ccb1c4948bcddeb1ffb8ad3b9f4e53b3a2))
- `frontend`: Add playlist track reordering UI and API support ([`dbfc31f`](https://git.platypush.tech/blacklight/songhive/commit/dbfc31fdcb391e00ff6ec11f9a2df0a53eee16e8))
- `files`: Add bulk file upload endpoint with size and count limits ([`393db57`](https://git.platypush.tech/blacklight/songhive/commit/393db57d47e702a76ccb350522196735cb29b189))
- `files`: Wire up bulk upload endpoint in the frontend ([`5ebb1ec`](https://git.platypush.tech/blacklight/songhive/commit/5ebb1ec3c6722325e568566abd224f2979158fcd))

### Changed

- `acl`: Batch track access checks with select-in queries ([`c48cea0`](https://git.platypush.tech/blacklight/songhive/commit/c48cea0b54283765d633afbc4ef66579f27b1a24))
- Update config example and architecture config keys ([`0852e58`](https://git.platypush.tech/blacklight/songhive/commit/0852e58d063859b5f901d1795e8784da04aeeb3e))

### Fixed

- `files`: Report upload progress when total is missing ([`a1f0452`](https://git.platypush.tech/blacklight/songhive/commit/a1f04526ff8787f2ecfde8ab59404f0d490c658f))
- `redis`: Use dedicated client for Tornado loop ([`653cdcc`](https://git.platypush.tech/blacklight/songhive/commit/653cdcc1a6fa21d38493d8f43a82684b1971fa77))

## 0.0.13

### Added

- `auth`: Add refresh-token session listing and revocation. ([`b519b11`](https://git.platypush.tech/blacklight/songhive/commit/b519b11a04009721d6fc2ec3d59608af1aebd80f))
- `auth`: Denylist access JWTs on session revocation. ([`f6f8e18`](https://git.platypush.tech/blacklight/songhive/commit/f6f8e184040e012cb619082fce7466c0905ca5dc))

### Fixed

- `stats`: Cast db aggregates to int for Redis cache. ([`8905919`](https://git.platypush.tech/blacklight/songhive/commit/890591928f73d0177519da67956282c69bd0c369))
- `docker`: Preserve client port in proxy Host header. ([`1d88ee0`](https://git.platypush.tech/blacklight/songhive/commit/1d88ee08c0eb51b22611265ff8515a3576219ae7))
- `storage`: Include all stored file references in orphaned cleanup. ([`24425ea`](https://git.platypush.tech/blacklight/songhive/commit/24425eaf7196346d5b63d42445f8c6671195f9ad))

## 0.0.12

### Added

- `i18n`: Add preview label to en locale. ([`f408805`](https://git.platypush.tech/blacklight/songhive/commit/f4088054d095102da4da4177ed80177f6c0a8db9))

### Changed

- `files-view`: Improved progress bar color contrast. ([`2777fd5`](https://git.platypush.tech/blacklight/songhive/commit/2777fd548d590f22d08d97d4c694e8348ac9a061))

### Fixed

- `db`: Use `NullPool` for the async engine by default so Tornado and a2wsgi
  request loops each create fresh asyncpg connections, preventing
  ``Future attached to a different loop`` errors
  during audio streaming. ([`1130aa8`](https://git.platypush.tech/blacklight/songhive/commit/1130aa8619b2e53e00909e887a9ee3f25f364979))

## 0.0.11

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
