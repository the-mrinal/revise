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

- User data isolation relies on Supabase Row Level Security — anything that lets one user read or write another user's rows is a critical bug.
- Auth uses Supabase magic links (JWT bearer tokens). Token handling lives in `server/auth.py` and `extension/capture-tokens.js`.
