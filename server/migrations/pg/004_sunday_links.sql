-- Revise accounts connected to Sunday (sunday.thelabs.wtf), one per account.
-- Sunday makes the token when a mentee clicks Connect Revise and keeps only
-- its hash; Revise keeps the token itself, to send that person's saves to
-- Sunday. Removing the row (either side's Disconnect) stops the saves.
CREATE TABLE IF NOT EXISTS sunday_links (
  user_id       uuid PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  sunday_token  text NOT NULL UNIQUE,
  sunday_login  text NOT NULL,                    -- the mentee's GitHub username, as Sunday knows it
  connected_at  timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE sunday_links ENABLE ROW LEVEL SECURITY;
