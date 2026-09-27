"""Move profile pictures out of Supabase Storage.

Downloads each avatar whose avatar_url still points at supabase.co into
AVATAR_DIR and rewrites the URL to the copy the server now serves at
/avatars/.... Safe to run more than once: rows already pointing at
/avatars/ are skipped. Run after deploying, before the database cutover:

  docker compose exec server python copy_avatars.py
"""

import os
import re
from datetime import datetime

import httpx

import db
from database import AVATAR_DIR

PUBLIC_PATH = re.compile(r"/storage/v1/object/public/avatars/([^/?]+)/avatar\.(\w+)")


def main() -> None:
    rows = db.fetch_all(
        "SELECT user_id, avatar_url FROM user_profiles WHERE avatar_url LIKE %s",
        ("%supabase.co%",),
    )
    print(f"{len(rows)} avatar(s) to copy")
    for row in rows:
        match = PUBLIC_PATH.search(row["avatar_url"])
        if not match or match.group(1) != row["user_id"]:
            print(f"  skip {row['user_id']}: unexpected URL {row['avatar_url']}")
            continue
        ext = match.group(2)
        resp = httpx.get(row["avatar_url"].split("?")[0], timeout=30, follow_redirects=True)
        resp.raise_for_status()
        folder = os.path.join(AVATAR_DIR, row["user_id"])
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, f"avatar.{ext}"), "wb") as f:
            f.write(resp.content)
        new_url = f"/avatars/{row['user_id']}/avatar.{ext}?v={int(datetime.utcnow().timestamp())}"
        # Only rewrite if nobody uploaded a new picture meanwhile.
        db.execute(
            "UPDATE user_profiles SET avatar_url = %s WHERE user_id = %s AND avatar_url = %s",
            (new_url, row["user_id"], row["avatar_url"]),
        )
        print(f"  copied {row['user_id']} ({len(resp.content)} bytes)")


if __name__ == "__main__":
    main()
