"""Authentication: Revise's own sessions, plus the Supabase pieces still in use.

Revise issues its own tokens, in the same shape clients already store:
  access_token   a JWT (HS256, iss "revise", aud "authenticated") carrying
                 sub = the user's id and email; valid for an hour
  refresh_token  "rv_" + random; only its SHA-256 is stored (sessions table);
                 valid for 180 days after its last use, and not rotated, since
                 the dashboard and the extension share one copy

Supabase tokens stay valid while Supabase Auth is still around: access tokens
until they expire, and a Supabase refresh token is exchanged once, through
Supabase, for a Revise session (the "bridge"). The exchange is remembered, so
the second holder of a shared token gets a Revise session too instead of
tripping Supabase's reuse detection.
"""

import hashlib
import os
import secrets
import time
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from supabase import create_client

import database
from database import ensure_user

# Only needed while SUPABASE_AUTH is on (email links, the session bridge).
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
SUPABASE_JWT_SECRET = os.environ.get("SUPABASE_JWT_SECRET", "")
SERVER_URL = os.environ.get("SERVER_URL", "http://localhost:8765")
REVISE_JWT_SECRET = os.environ["REVISE_JWT_SECRET"]
if len(REVISE_JWT_SECRET) < 32:
    raise RuntimeError("REVISE_JWT_SECRET must be at least 32 characters")

# "off" once Revise no longer relies on Supabase Auth: no email links, no
# Supabase access tokens, and a Supabase refresh token works only if it was
# already exchanged or imported (import_supabase_sessions.py).
SUPABASE_AUTH = os.environ.get("SUPABASE_AUTH", "on").strip().lower() != "off"
if SUPABASE_AUTH and not (SUPABASE_URL and SUPABASE_ANON_KEY and SUPABASE_JWT_SECRET):
    raise RuntimeError("SUPABASE_AUTH is on but SUPABASE_URL / SUPABASE_ANON_KEY / "
                       "SUPABASE_JWT_SECRET are missing; set them, or set SUPABASE_AUTH=off")

ISSUER = "revise"
ACCESS_TOKEN_SECONDS = 3600
SESSION_DAYS = 180
REFRESH_PREFIX = "rv_"

security = HTTPBearer()

_anon_client = None
_service_client = None
_jwks = None


def get_anon_client():
    global _anon_client
    if _anon_client is None:
        _anon_client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
    return _anon_client


def get_service_client():
    global _service_client
    if _service_client is None:
        _service_client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
    return _service_client


def get_jwks():
    global _jwks
    if _jwks is None:
        resp = httpx.get(f"{SUPABASE_URL}/auth/v1/.well-known/jwks.json")
        resp.raise_for_status()
        _jwks = resp.json()
    return _jwks


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def mint_access_token(user_id: str, email: str | None) -> str:
    now = int(time.time())
    claims = {"iss": ISSUER, "aud": "authenticated", "sub": user_id,
              "iat": now, "exp": now + ACCESS_TOKEN_SECONDS}
    if email:
        claims["email"] = email
    return jwt.encode(claims, REVISE_JWT_SECRET, algorithm="HS256")


def _session_expiry() -> str:
    return (datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)).isoformat()


def start_session(user_id: str, email: str | None, user_agent: str | None = None) -> dict:
    """A new Revise session: {access_token, refresh_token}."""
    refresh_token = REFRESH_PREFIX + secrets.token_urlsafe(32)
    database.insert_session(token_hash(refresh_token), user_id, _session_expiry(), user_agent)
    return {"access_token": mint_access_token(user_id, email), "refresh_token": refresh_token}


class SessionExpired(Exception):
    pass


def refresh(refresh_token: str, user_agent: str | None = None) -> dict:
    """New tokens for a refresh token: a Revise one, or a Supabase one (which
    becomes a Revise session). Raises SessionExpired when it's dead."""
    if refresh_token.startswith(REFRESH_PREFIX):
        row = database.use_session(token_hash(refresh_token), _session_expiry())
        if not row:
            raise SessionExpired("session ended or unknown")
        user = database.get_user(row["user_id"]) or {}
        return {"access_token": mint_access_token(row["user_id"], user.get("email")),
                "refresh_token": refresh_token}

    legacy_hash = token_hash(refresh_token)
    row = database.use_legacy_token(legacy_hash)
    if row:
        user_id = row["user_id"]
        email = (database.get_user(user_id) or {}).get("email")
    elif not SUPABASE_AUTH:
        raise SessionExpired("unknown session")
    else:
        try:
            supa = refresh_session(refresh_token)  # the bridge: once per token
        except Exception as e:
            raise SessionExpired(str(e)) from e
        claims = verify_token(supa["access_token"])
        user_id, email = claims["sub"], claims.get("email")
        ensure_user(user_id, email)
        database.remember_legacy_token(legacy_hash, user_id, "bridge")
    return start_session(user_id, email, user_agent)


def end_session(refresh_token: str) -> None:
    if refresh_token.startswith(REFRESH_PREFIX):
        database.revoke_session(token_hash(refresh_token))
    else:
        database.revoke_legacy_token(token_hash(refresh_token))


def sign_purpose_token(claims: dict, purpose: str, seconds: int) -> str:
    """A short-lived signed token for one step of a flow (e.g. linking
    GitHub). Its audience keeps it from ever passing as an access token."""
    now = int(time.time())
    return jwt.encode({**claims, "iss": ISSUER, "aud": purpose, "iat": now, "exp": now + seconds},
                      REVISE_JWT_SECRET, algorithm="HS256")


def read_purpose_token(token: str, purpose: str) -> dict | None:
    try:
        return jwt.decode(token, REVISE_JWT_SECRET, algorithms=["HS256"],
                          audience=purpose, issuer=ISSUER)
    except JWTError:
        return None


def verify_token(token: str) -> dict:
    """Verify an access token (Revise's or Supabase's). Returns the payload."""
    try:
        if jwt.get_unverified_claims(token).get("iss") == ISSUER:
            return jwt.decode(token, REVISE_JWT_SECRET, algorithms=["HS256"],
                              audience="authenticated", issuer=ISSUER)
        if not SUPABASE_AUTH:
            # The client refreshes (with its copied session) and carries on.
            raise JWTError("Supabase sessions are no longer accepted directly")
        # Try ES256 first (newer Supabase projects)
        header = jwt.get_unverified_header(token)
        if header.get("alg") == "ES256":
            jwks = get_jwks()
            payload = jwt.decode(
                token,
                jwks,
                algorithms=["ES256"],
                audience="authenticated",
            )
            return payload
        # Fall back to HS256 (older Supabase projects)
        payload = jwt.decode(
            token,
            SUPABASE_JWT_SECRET,
            algorithms=["HS256"],
            audience="authenticated",
        )
        return payload
    except JWTError as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {e}")


def get_current_claims(
    credentials: HTTPAuthorizationCredentials = Security(security),
) -> dict:
    """FastAPI dependency — returns the verified JWT payload (sub, email, ...)."""
    payload = verify_token(credentials.credentials)
    if not payload.get("sub"):
        raise HTTPException(status_code=401, detail="Token missing sub claim")
    # Accounts created in Supabase Auth get their users row on first use.
    ensure_user(payload["sub"], payload.get("email"))
    return payload


def get_current_user_id(
    credentials: HTTPAuthorizationCredentials = Security(security),
) -> str:
    """FastAPI dependency — extracts user_id from Bearer token's sub claim."""
    return get_current_claims(credentials)["sub"]


def send_magic_link(email: str, allow_new_accounts: bool = True) -> None:
    """Send a magic link email via Supabase Auth OTP. With GitHub sign-in
    available, links go to existing accounts only; new people use GitHub."""
    client = get_anon_client()
    client.auth.sign_in_with_otp(
        {"email": email, "options": {
            "email_redirect_to": f"{SERVER_URL}/api/auth/callback",
            "should_create_user": allow_new_accounts,
        }}
    )


def exchange_code_for_session(token_hash: str, type: str) -> dict:
    """Exchange a magic link token for a session (access + refresh tokens)."""
    client = get_anon_client()
    response = client.auth.verify_otp(
        {"token_hash": token_hash, "type": type}
    )
    return {
        "access_token": response.session.access_token,
        "refresh_token": response.session.refresh_token,
    }


def refresh_session(refresh_token: str) -> dict:
    """Refresh an expired session using a refresh token."""
    client = get_anon_client()
    response = client.auth._refresh_access_token(refresh_token)
    return {
        "access_token": response.session.access_token,
        "refresh_token": response.session.refresh_token,
    }
