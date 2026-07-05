# Contributing to Revise

Thanks for your interest in contributing! This project is a browser extension + FastAPI server for tracking coding practice with spaced repetition.

## Project layout

- `server/` — FastAPI backend (Python), Supabase for data + auth
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

To run the server you need a Supabase project and a `.env` file — see the [Self-host section of the README](README.md#self-host-for-developers). Then:

```bash
cd server
uvicorn main:app --reload --port 8765
```

### Running tests

Tests don't need a Supabase project — `server/tests/conftest.py` stubs the env vars and the auth dependency.

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

Schema changes go in `server/migrations/` as numbered `.sql` files (they are applied manually against Supabase — see the note in the README). Keep the full schema in the README's self-host section in sync.

## Adding platform support

Auto-detection patterns live in `server/patterns.py` (server) and `extension/patterns.js` (extension) — keep them in sync. Add a test in `server/tests/test_url_platform.py`.

## Questions

Open an issue or email dmrinal626@gmail.com.
