-- Revise's own sign-in: sessions it issues, accounts linked to GitHub, and
-- Supabase refresh tokens already exchanged for Revise sessions.
--
-- Tokens are never stored, only their SHA-256, so a copy of the database
-- can't be used to sign in as anyone.

CREATE TABLE IF NOT EXISTS sessions (
  id            text PRIMARY KEY,                 -- sha256 hex of the rv_ refresh token
  user_id       uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at    timestamptz NOT NULL DEFAULT now(),
  last_used_at  timestamptz NOT NULL DEFAULT now(),
  expires_at    timestamptz NOT NULL,             -- slides forward on every use
  revoked_at    timestamptz,
  user_agent    text
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions (user_id);

CREATE TABLE IF NOT EXISTS user_identities (
  id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  user_id           uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  provider          text NOT NULL,                -- 'github'
  provider_user_id  text NOT NULL,                -- GitHub's numeric id: stable, unlike the username
  login             text,                         -- GitHub username, refreshed on each sign-in
  email             text,
  linked_at         timestamptz NOT NULL DEFAULT now(),
  UNIQUE (provider, provider_user_id),
  UNIQUE (user_id, provider)                      -- one GitHub account per Revise account
);

-- Supabase refresh tokens that have been exchanged for Revise sessions
-- (source 'bridge'), or imported when Supabase is switched off ('import').
-- The dashboard and extension share one token, so a token may be exchanged
-- more than once; each exchange starts its own Revise session.
CREATE TABLE IF NOT EXISTS legacy_refresh_tokens (
  token_hash    text PRIMARY KEY,                 -- sha256 hex of the Supabase refresh token
  user_id       uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  source        text NOT NULL,
  created_at    timestamptz NOT NULL DEFAULT now(),
  last_used_at  timestamptz,
  revoked_at    timestamptz
);
CREATE INDEX IF NOT EXISTS idx_legacy_refresh_tokens_user ON legacy_refresh_tokens (user_id);

-- On Supabase these would otherwise be reachable through its REST API.
ALTER TABLE sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_identities ENABLE ROW LEVEL SECURITY;
ALTER TABLE legacy_refresh_tokens ENABLE ROW LEVEL SECURITY;
