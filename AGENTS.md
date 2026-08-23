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
- Tornado handles WebSocket connections (`/ws/events`) and audio streaming
  (`/api/v1/stream/{track_id}`) natively; all other routes fall through to
  FastAPI.
- Configuration priority: env vars (SONGHIVE_*) > CLI args > config.toml > defaults.
- The `pubby` library provides ActivityPub federation (FastAPI adapter).
- Tests use `pytest-asyncio` for async tests and `TestClient` for API tests.
- Frontend is a Vue.js 3 + TypeScript SPA in `frontend/`; builds to
  `songhive/static/`.
- Celery tasks are organized by domain: `tasks/import_.py`,
  `tasks/federation.py`, `tasks/transcoding.py`.
- When type-checking the Tornado + FastAPI bootstrap in `songhive/app.py`, the
  bridge through `a2wsgi.ASGIMiddleware` and `tornado.wsgi.WSGIContainer` can
  trigger structural mismatches because `FastAPI.__call__` uses Starlette's
  loose `MutableMapping`/`dict` ASGI types while `a2wsgi` uses strict TypedDict
  types. Cast to generic `Callable` signatures (e.g. with `typing.cast`) rather
  than suppressing with `# type: ignore`.

## Project Structure

```
songhive/          # Python backend package
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
