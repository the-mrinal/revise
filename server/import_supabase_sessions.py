"""Copy Supabase's still-valid sessions into Revise, so nobody is signed out
when Revise stops talking to Supabase.

Clients only ever hand their Supabase refresh token to Revise's server, never
to Supabase itself. So once the server stops calling Supabase, those tokens
never change again, and a copy of them (hashed, in legacy_refresh_tokens)
lets each one be exchanged for a Revise session later, even after the
Supabase project is gone.

Each valid token is copied along with the one before it: the dashboard and
the extension share a session, and one of them may still hold the older
token. Safe to run any number of times.

  docker compose exec server python import_supabase_sessions.py
"""

import psycopg

import db
from auth import token_hash


def main() -> None:
    with psycopg.connect(db.database_url("supabase"), prepare_threshold=None) as supa:
        rows = supa.execute(
            "SELECT r.token, r.parent, r.user_id FROM auth.refresh_tokens r "
            "JOIN auth.sessions s ON s.id = r.session_id "
            "WHERE NOT r.revoked AND (s.not_after IS NULL OR s.not_after > now())"
        ).fetchall()
    known = {r["id"] for r in db.fetch_all("SELECT id FROM users")}
    tokens = {}
    for token, parent, user_id in rows:
        if user_id not in known:
            continue
        for t in (token, parent):
            if t:
                tokens[token_hash(t)] = user_id
    added = 0
    for h, user_id in tokens.items():
        added += db.execute(
            "INSERT INTO legacy_refresh_tokens (token_hash, user_id, source) "
            "VALUES (%s, %s, 'import') ON CONFLICT (token_hash) DO NOTHING",
            (h, user_id),
        )
    users = len(set(tokens.values()))
    print(f"{len(rows)} valid Supabase sessions; {len(tokens)} tokens for {users} people; "
          f"{added} newly copied, {len(tokens) - added} already here.")


if __name__ == "__main__":
    main()
