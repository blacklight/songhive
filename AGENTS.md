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
- Tornado handles WebSocket connections (`/ws/events`) and audio streaming
  (`/api/v1/stream/{track_id}`) natively; all other routes fall through to
  FastAPI.
- Configuration priority: env vars (SONGHIVE_*) > CLI args > config.toml > defaults.
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
- Frontend is a Vue.js 3 + TypeScript SPA in `frontend/`; builds to
  `songhive/static/`. The Vite build also copies `swagger-ui-dist` into
  `songhive/static/swagger-ui/` (rewriting `swagger-initializer.js` to point
  at the app's `/openapi.json`); FastAPI mounts it at `/swagger-ui/` so no
  separate swagger-ui container is needed.
- The frontend toolchain (vite 8, vitest 5, jsdom 30 via undici 8) requires
  Node 24+; undici 8 calls `worker_threads.markAsUncloneable`, which does not
  exist on Node 20. CI and the Dockerfile `NODE_VERSION` both pin Node 24.
- Celery tasks are organized by domain: `tasks/import_.py`,
  `tasks/federation.py`, `tasks/tags.py`, `tasks/transcoding.py`,
  `tasks/notifications.py` (daily digest + seen-notification purge).
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
  `NotificationPreference`), `services/notifications.py`, and
  `api/routes/notifications.py`. `EventWebSocket.send_to_user`
  (`ws/events.py`) delivers targeted `notification` events; the frontend
  store is `frontend/src/stores/notifications.ts` on the shared `eventBus`
  from `frontend/src/api/ws.ts`.
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
  bulk tag rewrites incur download/upload transfer costs; consider routing
  `sync_track_tags` to a dedicated `tags` Celery queue with limited concurrency.
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
  stored; `Undo(Announce)` retracts it. Stale remote activities are pruned by
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
├── cli/           # Admin CLI
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

- Build and start all services (set `PUID`/`PGID` to the host user so
  containers and volumes are owned by the same UID/GID):

  ```bash
  export PUID=$(id -u)
  export PGID=$(id -g)
  docker compose up -d --build
  ```

- Stop all services:

  ```bash
  docker compose down
  ```

- Prepare volume directories manually (otherwise the `setup` service does it
  automatically at startup):

  ```bash
  PUID=$(id -u) PGID=$(id -g) ./scripts/setup-volumes.sh
  ```

- Restart after source changes:

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

Example:

```python
from .._common import client_ip
from ..deps import get_db, require_admin
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
        target_type="library",
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
