# Agent Notes

## Verification Commands

- Run the full test suite:

  ```bash
  python -m pytest
  ```

- Run specific tests:

  ```bash
  python -m pytest tests/test_api.py tests/test_config.py -v
  ```

- Run linting:

  ```bash
  python -m flake8 songhive tests
  ```

- Run import sorting checks:

  ```bash
  python -m isort --check-only songhive tests
  ```

- Apply automatic import sorting:

  ```bash
  python -m isort songhive tests
  ```

- Run type checking:

  ```bash
  python -m mypy songhive
  ```

- Run formatting checks:

  ```bash
  python -m black --check .
  ```

- Apply automatic formatting:

  ```bash
  python -m black .
  ```

- `pytest.ini` enables `pytest-xdist` (`-n auto`) by default, so
  `python -m pytest` now runs the suite in parallel. Use `-n 0` to force a
  sequential run when debugging.
- Note: running the full suite of tests takes time. You don't have to run the
  full suite of tests in the following cases:
  - If nothing has been modified since the beginning of the current
    conversation.
  - If only isort/black/flake8 changes have been applied - they don't impact
    the business logic.
  - If the changes you made are likely to only impact a small subset of tests,
    and only those tests should be run.

- On an externally managed system Python, install dev dependencies (including
  `fakeredis`) in a virtual environment and run the verification commands with
  that environment activated.

## Notes

- `setup.cfg` configures `flake8` with a max line length of 120, ignores
  `E203`, `W503`, `SIM104`, `SIM105`, `SIM115`, `B008`, and per-file ignores
  `I001`, `I005` (isort handled separately with `profile = black`). `B008` is
  suppressed because FastAPI's idiomatic `Query`/`Depends` defaults are
  evaluated once and reused intentionally.
- The backend uses FastAPI (ASGI) mounted inside Tornado via `a2wsgi`
  (ASGI-to-WSGI bridge). Falls back to uvicorn if `a2wsgi` is not available.
  The `WSGIContainer` is constructed with an explicit `ThreadPoolExecutor` —
  never drop it: without an executor (Tornado < 7 default) the WSGI app runs
  on the Tornado event-loop thread, serializing all requests and deadlocking
  any outbound signed federation fetch whose remote resolves our `keyId`
  back to this instance (the fetch-back can never be served while the loop
  is busy inside the triggering request).
- Tornado handles WebSocket connections (`/ws/events`), audio streaming
  (`/api/v1/stream/{track_id}`), and native HTTP stream mountpoints
  (`/streams/{mount}`) natively; all other routes fall through to
  FastAPI.
- Configuration priority: env vars (SONGHIVE_*) > CLI args > config.toml > defaults.
- The `songhive` CLI has one root argparse parser built by
  `songhive.cli.build_parser` (`cli/__init__.py`): core server options come
  from `config.loader.build_cli_parser`, and each subcommand module
  (`cli/admin.py`, `cli/stream_worker.py`, `cli/watch.py`) registers itself
  via an `add_parser(subparsers)` function. `app.main()` parses argv with the
  root parser (so `--help` lists the subcommands at every level) and then
  dispatches `sys.argv[2:]` to the matching `*_main` entry point — a
  subcommand must be the first argument, since server options are not
  forwarded to it.
- The `pubby` library provides ActivityPub federation (FastAPI adapter).
- Every remote HTTP fetch on the Webmention path (incoming source parsing,
  outgoing endpoint discovery, outgoing delivery, outgoing source reads)
  goes through the library's guarded fetch
  (`webmentions.handlers._fetch.fetch_guarded`, enabled via
  `ssrf_protection=True` in `create_webmentions_handler`) — it
  re-validates every redirect hop against non-public addresses and caps
  the streamed body at `webmentions.discovery_max_bytes`. This requires a
  `webmentions` release ≥ 0.1.26 — the param is silently swallowed on
  older versions, leaving fetches unguarded. In `tasks/webmentions.py`,
  `requests.RequestException` must keep propagating out of
  `process_incoming_webmention` — swallowing it disables `autoretry_for`
  and permanently drops mentions the sender believes were accepted.
- `create_webmentions_handler` also sets `exclude_local_targets=True` —
  the library then drops outgoing targets whose netloc matches the
  instance's `base_urls`, so self-delivered Webmentions never leave the
  mention pipeline that already handles them.
- Tests use `pytest-asyncio` for async tests and `TestClient` for API tests.
- The autouse `_no_real_celery_broker` fixture in `tests/conftest.py` stubs
  `celery_app.send_task` so test `.delay()`/`.apply_async()` calls never reach
  the configured broker. Without it, tests publish real messages that a running
  dev worker consumes against the real DB — where the test-only rows never
  exist — producing endless "not found"/"not committed yet" retry storms
  (`enrich_track`, `process_outgoing`, `fetch_preview_card`, `sync_track_tags`).
  Do NOT switch to `task_always_eager`: eager tasks call `load_config([])` and
  would hit the real database. Tests needing to assert enqueues can request the
  fixture by name and inspect the returned mock.
- Frontend is a Vue.js 3 + TypeScript SPA in `frontend/`; builds to
  `songhive/static/`. The Vite build also copies `swagger-ui-dist` into
  `songhive/static/swagger-ui/` (rewriting `swagger-initializer.js` to point
  at the app's `/openapi.json`); FastAPI mounts it at `/swagger-ui/` so no
  separate swagger-ui container is needed.
- The frontend toolchain (vite 8, vitest 5, jsdom 30 via undici 8) requires
  Node 24+; undici 8 calls `worker_threads.markAsUncloneable`, which does not
  exist on Node 20. CI and the Dockerfile `NODE_VERSION` both pin Node 24.
- In a git worktree (`.worktrees/*`), `songhive/static/` is not built, so the
  SPA-serving tests (`tests/test_app.py`, `tests/test_semantic_meta.py`,
  `tests/test_home_page.py`, feed-link injection tests in `tests/test_feeds.py`)
  fail with 404 — environmental, not a regression. The worktree's
  `frontend/node_modules` is a symlink into the main checkout, so invoke
  frontend tools via `PATH=$PWD/node_modules/.bin:$PATH <tool>` (plain
  `npm run` may not resolve `.bin` correctly from the worktree).
- Celery tasks are organized by domain: `tasks/import_.py`,
  `tasks/federation.py`, `tasks/tags.py`, `tasks/transcoding.py`,
  `tasks/notifications.py` (daily digest + seen-notification purge),
  `tasks/scrobbling.py` (Last.fm/Libre.fm submissions).
- Celery runs three queues (`tasks/celery.py` `task_routes`): `celery`
  (default — interactive/low-volume work), `scrobbles` (`tasks/scrobbling.*`
  — latency-sensitive submissions) and `bulk` (per-item library fan-out:
  `musicbrainz.*`, `images.*`, `tags.*`, `import_.*`, `transcoding.*`,
  `external_libraries.sync_external_library`/`write_back_metadata`,
  `podcasts.refresh_podcast`, `storage.rehash_audio_files`). A fresh external
  library sync enqueues tens of thousands of enrichment jobs — without the
  split they starve scrobbles, federation delivery and notifications for
  hours. Workers MUST consume all three: `celery -A songhive.tasks worker
  -Q celery,scrobbles,bulk` (compose and `config/systemd/songhive-celery.service`
  already do). Routing happens at publish time, so a worker that predates the
  split still drains messages already queued on `celery`.
- Scrobbling (`services/scrobbler.py`, `tasks/scrobbling.py`,
  `api/routes/scrobbling.py`) targets Audioscrobbler-compatible services
  (Last.fm `https://ws.audioscrobbler.com/2.0/`, Libre.fm
  `https://libre.fm/2.0/`). Instance API key pairs live under
  `[scrobbling]`; a service is only offered when both key and secret are
  set. Users authenticate via `auth.getMobileSession` — the password is
  exchanged for a session key which is Fernet-encrypted on the
  `scrobble_configs` row (`models/scrobble.py`) and never returned by
  the API. `track.updateNowPlaying` is enqueued only on explicit play
  reports — `POST /api/v1/scrobbling/now-playing/{track}` (the web player
  calls it on the first `play` event after `load`) and
  `scrobble.view?submission=false`. Stream/download requests never
  trigger it: clients prefetch upcoming tracks (Substreamer caches the
  whole album ahead), so bytes served are not a play signal —
  `SubsonicStreamHandler` also disables the streamed-byte listen
  recording (`_record_listen_if_needed` is a no-op) and never writes the
  `getNowPlaying` registry from `stream.view`.
  `track.scrobble` is enqueued inside `services.streaming.record_listen`,
  the funnel every listen path (web player history report, streamed-byte
  threshold, `scrobble.view?submission=true`) passes through;
  `record_listen` accepts an optional `played_at` so queued Subsonic
  submissions (`time` param, ms epoch, positional per `id`) keep the
  real play timestamp. Both tasks
  deduplicate in Redis (`songhive:scrobble:{np,sub}:{user}:{track}` —
  the same play reaches `record_listen` twice within seconds). The
  per-user `min_seconds`/`min_percent` thresholds are exposed through
  `GET /api/v1/scrobbling/` and drive both the web player's
  `HistoryReporter` and the stream handler's streamed-seconds threshold;
  unconfigured users keep the historical defaults (30s server-side, 50%
  client-side). The streamed-seconds estimate
  (`_StreamState.started_at`/`_elapsed_seconds`) is capped by wall-clock
  elapsed since the stream started — bytes only approximate playback
  position while delivery is consumption-limited, so a client that
  buffers the whole file in seconds must not trip the threshold early.
  Remote (federated) tracks scrobble through the same machinery: the web
  player reports `POST /api/v1/remote/objects/{id}/now-playing` on play
  and `POST /api/v1/remote/objects/{id}/listen` at the threshold —
  `services.streaming.record_remote_listen` writes a `listening_history`
  row keyed on `remote_object_id` (the `track_id`/`remote_object_id` XOR
  constraint) and enqueues the same Celery tasks with
  `entity_kind="remote"`. `_load_scrobble_fields` then resolves metadata
  from the `RemoteObject` payload (`scrobbler.remote_object_fields`),
  folding `Audio`/`Video` renditions onto their media entity so the
  scrobbled title is the plain track title.
- Personal listening stats live under `/api/v1/stats`
  (`api/routes/stats.py` + `services/listening_stats.py`, frontend
  `views/StatsView.vue` + `components/stats/` on ECharts via `vue-echarts`).
  Top-N lists and release-year grouping run in SQL; calendar/hour buckets
  are computed in Python over a `created_at` range scan so the code is
  portable between PostgreSQL and SQLite and honors the caller's IANA
  `tz` param. Remote listens count everywhere except genre/release-year
  charts (no remote metadata); rendition rows fold onto their
  `media_of_url` entity for artist/album attribution. All queries ride
  the `ix_listening_history_user_created_at` composite index — never
  drop it when touching `models/history.py`.
- RSS/Atom feeds live under `/feeds` (outside `/api/v1`):
  `api/routes/feeds.py` + `services/feeds.py` render the XML, and
  `api/semantic_meta.py` / `api/routes/profile_pages.py` inject the
  `<link rel="alternate">` discovery tags into served pages;
  `frontend/src/composables/useFeedLinks.ts` keeps them in sync after
  client-side navigation. Feed queries must keep honouring the requester
  ACL — anonymous readers only see `public` content. Artists have no
  `visibility`/`owner_id` columns and are treated as public containers
  everywhere, including `acl.can_access`.
- User notifications live in `models/notification.py` (`Notification`,
  `NotificationPreference`, `ActivitySubscription`),
  `services/notifications.py`, and `api/routes/notifications.py`.
  `EventWebSocket.send_to_user` (`ws/events.py`) delivers targeted
  `notification` events; the frontend store is
  `frontend/src/stores/notifications.ts` on the shared `eventBus` from
  `frontend/src/api/ws.ts`. The profile "bell" is an
  `ActivitySubscription` (`POST`/`DELETE
  /api/v1/users/{username}/activity-subscription` for local users,
  `/api/v1/remote/actors/{handle}/activity-subscription` for remote
  actors, keyed on `target_actor_url`); subscribing also follows the
  target so remote activities keep arriving. Every local activity
  producer calls `_notify_activity_subscribers`
  (`services/activities.py`), which fans out `activity` notifications to
  subscribers after a `can_view_activity` check; the remote counterpart
  `notify_remote_activity_subscribers` is invoked by the inbox
  materializers (`_materialize_remote_object`,
  `materialize_remote_announce`, `materialize_remote_post` via the
  `notify_subscribers` flag on `_materialize_remote_activity`).
- Frontend specs that mount `NotificationActivityCard` must reset the
  module-level `fetchActivityCached` map with `clearActivityCache()`
  (`utils/activityFetch.ts`) in `beforeEach`, or cached fetches leak
  across tests and leave `getActivity` once-mocks unconsumed.
- Audio file storage uses audio-only SHA-256 hashing (via ffmpeg `streamhash`)
  so that tags and cover art can be rewritten without changing the stored path
  or invalidating the content hash. Run `songhive admin rehash-audio` once to
  migrate legacy audio `StoredFile` rows to audio-only hashes.
- Track metadata is rewritten into embedded tags by the `sync_track_tags`
  Celery task (`tasks/tags.py`). It resolves cover art in the order
  track image → album cover → none, acquires a Redis lock per track, and
  re-uploads the file in place for S3 backends.
- Manual tag sync can be triggered via `songhive admin sync-tags` or
  `POST /api/v1/admin/sync-tags`. For large S3 libraries, the migration and
  bulk tag rewrites incur download/upload transfer costs; `sync_track_tags`
  already routes to the `bulk` queue — for very large S3 libraries consider
  a dedicated worker with limited concurrency consuming just that queue.
- Celery worker tasks run their async work inside ``asyncio.run(...)``. Each
  ``asyncio.run`` creates and closes a new event loop, and ``asyncpg``
  connections are bound to the loop that opened them. Every task that uses
  ``get_session()`` must therefore call ``await dispose_and_reset()`` in the
  same ``finally`` block that closes the session, so the next task starts with
  a fresh engine instead of reusing connections tied to a closed loop.
- ``EventWebSocket._connections`` is process-local: ``broadcast`` and
  ``send_to_user`` only reach sockets in their own process. Both also publish
  an envelope to the Redis pub/sub channel ``songhive:ws-events``, and the
  Tornado process runs ``ws_event_subscriber`` to deliver envelopes from
  other processes (e.g. Celery workers creating notifications). Publishing
  uses a synchronous Redis client so it works from any loop or thread.
- Read-only media endpoints (`/api/v1/files/{id}/download`,
  `/api/v1/tracks/{id}/download`, `/api/v1/stream/{id}`) intentionally return
  `Access-Control-Allow-Origin: *` so remote Fediverse clients can embed audio.
  `MediaCorsMiddleware` (`api/middleware/media_cors.py`) is registered after
  `CORSMiddleware` so it runs first and answers media-path preflights before
  the allowlist middleware can reject them; requests from configured
  `cors_origins` are passed through so credentialed CORS keeps working. The
  Tornado `StreamHandler` sets the same headers itself via
  `set_default_headers` (it bypasses FastAPI middleware entirely).
- Public entities can be embedded on third-party pages via the share
  dialog's "Embed" tab (`components/share/EmbedPanel.vue` + snippet builders
  in `utils/embed.ts`): `<audio>`/`<div>` HTML, Markdown link, `<script>`
  widget, or `<iframe>`. The `<script>` and `<iframe>` formats render the
  SPA's standalone `/embed/{type}/{id}` route (`views/EmbedView.vue`) —
  `frontend/public/embed.js` is served verbatim as `/embed.js`, turns
  `data-songhive-embed` placeholders into iframes, and resizes them from
  the `songhive-embed` `postMessage` protocol the view emits. Collection
  `<audio>`-list embeds are only offered below 250 elements and always
  filter to public tracks with a playable `audio_url`.
- Browser sessions use server-managed cookies, not JS-readable tokens:
  login/refresh set `HttpOnly` `access_token` + `refresh_token` cookies
  (refresh scoped to `Path=/api/v1/auth`) plus a readable `csrf_token`
  cookie (`api/cookies.py`). `CsrfMiddleware` (`api/middleware/csrf.py`)
  rejects unsafe requests that carry an auth cookie without a matching
  `X-CSRF-Token` header; `Authorization` requests, safe methods, and the
  session endpoints are exempt. The frontend (`api/client.ts`) sends
  `credentials: "same-origin"` and the CSRF header automatically; stream and
  WebSocket URLs carry no token — `StreamHandler` and `EventWebSocket` both
  accept the `access_token` cookie. Bearer auth and JSON token responses are
  kept for non-browser clients.
- Users pick a `profile_visibility` (`public`/`local`/`private`, default
  `public`) from `/settings`. `GET /api/v1/users` and the users section of
  `GET /api/v1/search/` share `services.auth.list_public_users`, which shows
  `local` profiles only to authenticated callers and never lists `private`
  profiles (not even to their owner). Individual profile pages stay reachable
  regardless.
- External libraries (`songhive/external/`) support `s3`
  (`external/_s3.py`, aioboto3), `sftp` (`external/_sftp.py`, asyncssh),
  `webdav` (`external/_webdav.py`, httpx), `dropbox`
  (`external/_dropbox.py`, httpx — Dropbox HTTP API v2, OAuth access token or
  `refresh_token`+`app_key`+`app_secret` grant with a process-wide
  `_TOKEN_CACHE`), and `gdrive` (`external/_gdrive.py`, httpx — Drive API v3,
  service account JWT grant or OAuth `refresh_token`+`client_id`+`client_secret`,
  per-instance `_token_cache`) providers alongside `local`/`fake`.
  Two traps when touching that code: (1) API responses redact secret config
  keys to `"<redacted>"`, so PATCH routes must run submitted configs through
  `_merge_config_preserving_redacted` or credentials get overwritten with the
  sentinel; (2) sync relies on the adapter's `detect_changes` capability —
  items whose stored `provider_etag` (or mtime+size) still matches the listing
  skip hashing/metadata reads entirely, so adapters must only advertise
  `detect_changes` when listing metadata is a reliable change token (S3
  uses the object ETag; WebDAV uses ETag/mtime/size; Dropbox uses `rev`; the
  fake adapter's etag covers payload+metadata). The filesystem watchdog
  (`external/watchdog.py`) only applies to `local` libraries; remote-provider
  freshness comes from scheduled syncs via `scan_scheduled_syncs_task`.
  Dropbox's `content_hash` is stored on `ExternalItemRef.checksum` but is
  NOT a sha256 — the adapter advertises `checksum_algorithm="dropbox"` so
  `sync._resolve_sha256` never treats it as an audio hash. Changing an
  external library's visibility must go through
  `services.music.propagate_external_library_visibility` so synced `Track`
  rows stay consistent with the backing `Library`.
  OAuth-capable providers register an `OAuthProviderSpec` in
  `external/oauth.py` (Dropbox and Google Drive do today;
  Spotify/Tidal/YouTube are planned): the generic begin/callback/claim routes
  (`/api/v1/external-libraries/oauth/*`) keep pending flows and granted
  config fragments in short-lived Redis keys bound to the initiating user,
  and the SPA's "Connect" button merges the claimed fragment into the form
  for a normal save.
- Outbound follows (a local user following a local or remote actor) live in
  the `follows` table (`models/follow.py`) — pubby's
  `federation_followers`/`federation_follow_requests` tables only track the
  inbound side. `services/follows.py` resolves targets, delivers
  `Follow`/`Undo(Follow)`, and folds inbound `Accept`/`Reject` back into the
  row. Inbound `Create` and `Announce` activities are only materialized when
  the actor is followed by a local user (`actor_is_followed`); explicit
  remote-URL lookups are the other admission path. An `Announce` of an
  object unknown locally is dereferenced through
  `remote_content.dereference_remote_object` (guarded fetch →
  `remote_objects` row + mirror `Activity`) before the `announce` row is
  stored; `Undo(Announce)` retracts it.
- Remote music entities (`remote_objects` rows with a `resource_type`) are
  first-class members without local catalog rows: `favorites`,
  `library_tracks`, and `playlist_tracks` each carry a nullable
  `remote_object_id` FK alongside `track_id` with an exactly-one-reference
  check constraint. `remote_content.remote_collection_object_ids` computes
  the collection closure (collected/favorited seeds + cached descendants +
  ancestors) so a collected remote track surfaces its album and artist;
  `resolve_remote_track_ids` expands remote containers into cached track
  descendants on playlist/library adds. Interactions reuse the `Activity`
  machinery — `ensure_remote_activity` lazily materializes a synthetic
  `Create` mirror (`POST /api/v1/remote/objects/{id}/activity`), and
  responses carry `activity_id`/`favorited`/`in_collection` viewer state.
  `GET /api/v1/remote/objects` supports `collection`, `favorites`, and
  `library` filters; tombstoned rows are excluded from playback and
  membership expansion. Stale remote activities are pruned by
  `tasks.federation.prune_remote_activities` / `songhive admin
  prune-remote-activities` / `POST /api/v1/admin/federation/prune-remote-activities`,
  gated on `federation.remote_activity_retention_days` and scheduled via
  `federation.remote_activity_prune_schedule`.
- Pubby's storage is synchronous and writes on its own connection while a
  request's async session may still hold an open transaction. On SQLite this
  deadlocks into "database is locked": any code path that writes through
  pubby storage mid-request (e.g. `_follow_local`, follow-request accept)
  must run before the session accumulates uncommitted writes. Provision
  actor keys up front (`ensure_user_actor` is a no-op for users created
  with federation enabled) rather than lazily inside such paths.
- Users pick a `followers_approval` policy (`accept`/`manual`/`reject`,
  default `accept`) from `/settings`. `tasks/federation.process_incoming`
  passes pubby's `InboxProcessor` a `follow_policy` callback that maps the
  Follow target's actor URL to the owner's setting; `manual` stores a
  pending `FollowRequest` in pubby's `federation_follow_requests` table and
  flags the `follow` notification's payload with `follow_request_pending`.
  Requests resolve via `POST /api/v1/users/me/follow-requests/accept|reject`
  (owner-only), which delegates to pubby's `accept_follow_request`/
  `reject_follow_request` and enqueues the reply on `deliver_activity`.
  These pubby APIs only exist in the local `~/git_tree/pubby` checkout —
  deployments need a pubby release that includes them.
- Mention completion for remote users goes through `GET /api/v1/search/` with
  `remote_users=1`: the `users` section then merges cached remote actors via
  `services.federation.search_remote_actors`, which queries pubby's
  `federation_followers` and `federation_actor_cache` tables directly through
  `storage.session_factory`/`storage.follower_model`/`storage.actor_cache_model`
  (sync — call via `asyncio.to_thread`). Actors on the instance domain or
  blocked domains are excluded, and matches carry a `user@domain` `name` that
  `services.mentions` resolves through WebFinger at post time.
- When type-checking the Tornado + FastAPI bootstrap in `songhive/app.py`, the
  bridge through `a2wsgi.ASGIMiddleware` and `tornado.wsgi.WSGIContainer` can
  trigger structural mismatches because `FastAPI.__call__` uses Starlette's
  loose `MutableMapping`/`dict` ASGI types while `a2wsgi` uses strict TypedDict
  types. Cast to generic `Callable` signatures (e.g. with `typing.cast`) rather
  than suppressing with `# type: ignore`.
- Foreign media APIs (Subsonic today; Icecast/Mopidy/Jellyfin planned) plug in
  through `songhive/adapters/`: `APIAdapter` declares `is_enabled`, `router()`
  (FastAPI routes mounted at app creation) and `tornado_routes()` (native
  handlers installed before the WSGI fallback). The Subsonic adapter owns the
  `/rest` namespace — `/rest/` is exempt from `CsrfMiddleware` because
  Subsonic clients authenticate via `u`/`p`/`apiKey` request params, not
  cookies. FastAPI matches routes in registration order, so the
  `/rest/{method}.view` catch-all is registered last via
  `routes.register_fallback_route()` (called from `SubsonicAdapter.router()`
  after `media.py` registers its binary endpoints on the same router).

## Project Structure

```
songhive/          # Python backend package
├── adapters/      # Foreign API adapters (Subsonic at /rest, registry + base)
├── api/           # FastAPI app + routes
├── cli/           # CLI subcommands (admin, stream-worker, watch-external-libraries)
├── config/        # Configuration (Pydantic settings + TOML loader)
├── federation/    # ActivityPub (pubby) integration
├── migrations/    # Alembic migration scripts
├── models/        # SQLAlchemy models
├── music/         # Music import & metadata
├── services/      # Business logic
├── storage/       # Storage backends (local, S3)
├── streaming/     # Tornado stream handler + ffmpeg transcoder
├── tasks/         # Celery tasks
├── users/         # User management
├── ws/            # WebSocket handlers
└── version.py

frontend/          # Vue.js 3 + TypeScript SPA
tests/             # pytest test suite
docs/              # Architecture & feature documentation
```

When notable sections are added, changed or removed, remember to update
`docs/ARCHITECTURE.md` accordingly.

## Database Migrations

- Migrations are managed with [Alembic](https://alembic.sqlalchemy.org/).
- The initial ``base`` revision is intentionally empty: databases deployed
  before this change are treated as the baseline, and fresh installs get the
  current schema from SQLAlchemy and are then stamped at ``head``.
- Migrations are run automatically when the application starts (inside
  ``create_app``) and before ``songhive``/``celery`` starts in Docker.
- ``ensure_migrated`` is safe for concurrent callers: it uses a PostgreSQL
  advisory lock or a SQLite ``fcntl`` file lock so multiple containers (or
  processes) starting at once do not race to create the baseline schema.
- Run migrations manually via the admin CLI:

  ```bash
  python -m songhive admin migrate
  ```

- Create a new migration after a model change (from the repository root, with
  ``SONGHIVE_DATABASE__URL`` or a valid ``config.toml``):

  ```bash
  alembic revision --autogenerate -m "add example column"
  ```

- Verify migration status:

  ```bash
  alembic current
  alembic history
  ```

## Docker

- Start all services (set `PUID`/`PGID` in `.env` — copied from
  `.env.example` — or export them, so containers and volumes are owned by the
  same UID/GID as the host user). By default the published image is pulled;
  `--build` only does something if the `build:` blocks in
  `docker-compose.yml` are uncommented for a local build:

  ```bash
  docker compose up -d --build
  ```

- Stop all services:

  ```bash
  docker compose down
  ```

- Prepare volume directories manually (otherwise the `setup` service does it
  automatically at startup — its commands are inlined in `docker-compose.yml`,
  no script mount needed):

  ```bash
  PUID=$(id -u) PGID=$(id -g) ./scripts/setup-volumes.sh
  ```

- Restart after source changes (requires uncommented `build:` blocks to have
  any effect):

  ```bash
  docker compose up -d --build songhive worker
  ```

- View logs:

  ```bash
  docker compose logs -f
  ```

- The stack exposes:
  - Web UI and API: http://localhost/
  - OpenAPI JSON: http://localhost/openapi.json
  - Swagger UI: http://localhost/swagger-ui/

- The Nginx reverse proxy resolves backend service hostnames through Docker's
  embedded DNS (`127.0.0.11`) so it keeps working when containers are
  recreated.

## Audit Trails

When adding or editing admin- or library-related features that mutate state,
record an audit log entry with `songhive.services.audit.log_action`.

- Use the shared `client_ip` helper from `songhive.api._common` for the
  `ip_address` argument.
- Use `require_admin` (or another authenticated dependency) to obtain the
  `actor_id`.
- Keep action names in `domain.verb` form, e.g.:
  - `library.create`, `library.update`, `library.delete`
  - `library_track.add`, `library_track.remove`
  - `user.promote`, `user.demote`, `user.activate`, `user.deactivate`
  - `report.resolve`, `invite.create`, `invite.revoke`
- Always include a `target_type` and `target_id` when one exists, and put
  relevant before/after values in `details`.
- `target_type` values come from the `AuditTargetType` enum in
  `songhive/models/audit_log.py` — pass a member, not a raw string.
  `log_action` rejects unknown values, and
  `GET /api/v1/admin/audit/target-types` exposes the enum so the admin
  audit page's target-type dropdown stays in sync automatically. Add a
  member there (plus an `pages.admin.audit.targetTypes.*` label in
  `frontend/src/i18n/locales/en.json`) when a new target type is needed.

Example:

```python
from .._common import client_ip
from ..deps import get_db, require_admin
from ...models.audit_log import AuditTargetType
from ...services import audit

@router.post("/libraries/{library_id}", ...)
async def update_library(
    library_id: str,
    body: LibraryUpdateRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    library = await music.update_library(db, library_id, body)
    await audit.log_action(
        db,
        actor_id=admin.id,
        action="library.update",
        target_type=AuditTargetType.LIBRARY,
        target_id=library.id,
        details={"name": library.name, "visibility": library.visibility},
        ip_address=client_ip(request),
    )
    return LibraryResponse.model_validate(library)
```

- Add/update tests that assert an `AuditLog` row is created with the expected
  `action`, `actor_id`, and `target_id`.
- For bulk actions, prefer a single `user.bulk_action` (or domain-specific)
  entry with the list of affected IDs and the action type in `details`.
  Where the implementation contract specifies per-item entries (e.g. the
  admin bulk user endpoints use `user.bulk_deactivate`, `user.bulk_activate`,
  and `user.bulk_delete` per user), follow the contract.

## Audio Streams

- The `stream-worker` command (`songhive stream-worker`) runs a dedicated process
  that owns long-lived server-side output drivers. It is registered in
  `songhive/app.py` and ships in the Docker Compose `streams` profile and as a systemd unit at `config/systemd/songhive-stream-worker.service`.
- Each `SessionDriver` generates a unique lock token and uses Redis
  `songhive:stream:lock:{output_id}` to ensure only one worker drives an output.
  The lock is refreshed on every main-loop iteration and released on shutdown.
- Worker tests live in `tests/test_streams_worker.py`; output/playback tests in
  `tests/test_api_outputs.py` and `tests/test_api_playback.py`.
- The `http` provider (`songhive/streams/http.py`) serves Icecast-style
  mountpoints without an external server. The worker's encoder writes encoded
  chunks to the capped Redis stream `songhive:stream:data:{mount}`; the Tornado
  `StreamMountHandler` (`songhive/streaming/mount.py`) serves
  `GET /streams/{mount}` by bursting the newest entries (`XREVRANGE`) then
  following the stream (`XREAD BLOCK`), so each listener is an independent
  cursor and Redis does the fan-out. Entries older than
  `streams.http_stream_max_lag_seconds` (entry IDs are server ms timestamps)
  are skipped so a lagging listener jumps forward instead of accumulating
  latency, and `X-Accel-Buffering: no` keeps buffering proxies (nginx) from
  hiding that lag in their own buffers. Liveness is the TTL'd
  `songhive:stream:meta:{mount}` key refreshed by the driver; an
  `{"end": "1"}` stream entry disconnects listeners on graceful stop, and
  per-listener `songhive:stream:listener:{mount}:{id}` TTL keys feed
  `driver.listener_count()` for idle shutdown. Mount slugs must be unique
  across all `http` outputs (enforced in `services/outputs.py`), may carry an
  optional `listen_token` (`?token=`/`Bearer`), and are unreachable in the
  uvicorn fallback like the other native Tornado routes.
- The `snapcast` provider (`songhive/streams/snapcast.py`) casts to a
  snapserver via an ffmpeg *passthrough* encoder that copies raw s16le PCM to
  a `pipe://` source FIFO (`mode=fifo`, auto-created with `mkfifo`; existing
  non-FIFO paths are rejected both at validation and driver start so a typo
  can never clobber a regular file) or to a `tcp://` listening source
  (`mode=tcp`, which allows a remote snapserver). The snapserver source must
  be declared in `snapserver.conf` — Snapcast registers no streams
  dynamically, and the TCP `port` is the `tcp://` source listener, not the
  snapclient port 1704. `listener_count` polls snapserver's JSON-RPC
  `Server.GetStatus` over raw TCP (`control_host`/`control_port`, default
  1705; 1780 is the HTTP/snapweb port) and counts connected, unmuted clients
  — optionally filtered to groups playing `stream_name` — so idle shutdown
  tracks real listeners. `streams.allowed_output_hosts` applies to the
  snapcast TCP target the same way as to Icecast hosts.
