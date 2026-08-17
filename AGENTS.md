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

- Run formatting checks:

  ```bash
  python -m black --check .
  ```

- Apply automatic formatting:

  ```bash
  python -m black .
  ```

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
- Configuration priority: env vars > CLI args > config.toml > defaults.
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
├── config/        # Configuration (Pydantic settings + TOML loader)
├── models/        # SQLAlchemy models
├── services/      # Business logic
├── federation/    # ActivityPub (pubby) integration
├── users/         # User management
├── music/         # Music import & metadata
├── streaming/     # Tornado stream handler + ffmpeg transcoder
├── storage/       # Storage backends (local, S3)
├── tasks/         # Celery tasks
├── ws/            # WebSocket handlers
└── cli/           # Admin CLI

frontend/          # Vue.js 3 + TypeScript SPA
tests/             # pytest test suite
docs/              # Architecture & feature documentation
```
