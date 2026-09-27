# Security Policy

## Supported versions

Only the latest release (and the `main` branch, which runs at [revise.mrinal.dev](https://revise.mrinal.dev)) receives security fixes.

## Reporting a vulnerability

Please **do not open a public issue** for security vulnerabilities.

Instead, either:

- Use [GitHub private vulnerability reporting](https://github.com/the-mrinal/revise/security/advisories/new), or
- Email **dmrinal626@gmail.com** with a description and reproduction steps

You should get a response within a few days. Once the issue is confirmed and fixed, it will be disclosed in the release notes.

## Scope notes

- User data isolation is enforced by the server: every query is scoped to the signed-in account (`server/database.py`). Anything that lets one user read or write another user's rows is a critical bug.
- Sign-in is via GitHub; Revise issues its own sessions (JWT access tokens, SHA-256-hashed refresh tokens). Token handling lives in `server/auth.py`, `server/main.py` (the `/api/auth/*` routes) and `extension/capture-tokens.js`.
