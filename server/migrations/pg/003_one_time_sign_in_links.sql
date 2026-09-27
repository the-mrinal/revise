-- One-time sign-in links an admin can create for someone who can't use
-- GitHub (the replacement for Supabase's email links). The link itself is a
-- signed token; this table only records that it has been used, so it works
-- once.
CREATE TABLE IF NOT EXISTS used_sign_in_links (
  jti         text PRIMARY KEY,
  user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  used_at     timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE used_sign_in_links ENABLE ROW LEVEL SECURITY;
