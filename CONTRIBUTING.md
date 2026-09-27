# Contributing to Revise

Thanks for your interest in contributing! This project is a browser extension + FastAPI server for tracking coding practice with spaced repetition.

## Project layout

- `server/` — FastAPI backend (Python) on Postgres, with GitHub sign-in
- `extension/` — Chrome/Safari extension (Manifest V3, vanilla JS)
- `thoughts/shared/research/` — DSA pattern study guides served on the `/research` page
- `docs/` — README images

## Development setup

### Server

Requires Python 3.10+ (3.11 matches the production image).

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r server/requirements-dev.txt
```

To run the server you need a `.env` file (a GitHub OAuth app for sign-in; Postgres comes with `docker compose`) — see the [Self-host section of the README](README.md#self-host-for-developers). Then:

```bash
cd server
uvicorn main:app --reload --port 8765
```

### Running tests

Route tests need nothing external — `server/tests/conftest.py` stubs the env vars and the auth dependency. Database tests need a Postgres server: set `TEST_DATABASE_URL` to a superuser connection string (e.g. `docker run -d -e POSTGRES_PASSWORD=test -p 55432:5432 postgres:17.6` and `TEST_DATABASE_URL=postgresql://postgres:test@localhost:55432/postgres`); without it they're skipped.

```bash
cd server
pytest
```

### Linting

```bash
ruff check server/
```

### Extension

1. Open `chrome://extensions`, enable **Developer mode**
2. **Load unpacked** → select the `extension/` folder
3. After editing files, click the reload icon on the extension card

To point it at a local server, update the server URL in the extension config.

## Pull requests

1. Fork the repo and create a branch from `main`
2. Make your change; add or update tests under `server/tests/` for server changes
3. Make sure `pytest` and `ruff check server/` pass locally — CI runs both on every PR
4. Open a PR with a clear description of what and why

Direct pushes to `main` are blocked; all changes go through PRs with green CI.

## Database migrations

Schema changes go in `server/migrations/pg/` as numbered `.sql` files; they apply automatically when the server starts (`server/migrate.py`).

## Adding platform support

Auto-detection patterns live in `server/patterns.py` (server) and `extension/patterns.js` (extension) — keep them in sync. Add a test in `server/tests/test_url_platform.py`.

## Questions

Open an issue or email dmrinal626@gmail.com.
