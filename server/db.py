"""Postgres connection pool and small query helpers.

Rows come back as plain dicts shaped exactly like the Supabase REST API
returned them (timestamps and dates as ISO strings, UUIDs as strings,
jsonb as dicts), because the rest of the server — and the dashboard and
extension — compare, sort, and slice those strings.

Two databases can be configured during the Supabase exit:
  SUPABASE_DB_URL  the Supabase Postgres (source, until the cutover)
  DATABASE_URL     our own Postgres (destination, and the only one afterwards)
Which one serves requests is decided by cutover.current_target().
"""

import os
import threading
import uuid
from datetime import date, datetime

from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

TARGETS = {"local": "DATABASE_URL", "supabase": "SUPABASE_DB_URL"}

_pool: ConnectionPool | None = None
_pool_target: str | None = None
_lock = threading.Lock()


def database_url(target: str) -> str:
    env = TARGETS[target]
    url = os.environ.get(env)
    if not url:
        raise RuntimeError(f"{env} is not set (needed for database target '{target}')")
    return url


def _open_pool(target: str) -> ConnectionPool:
    return ConnectionPool(
        database_url(target),
        min_size=1,
        max_size=int(os.environ.get("DB_POOL_MAX", "5")),
        kwargs={
            "autocommit": True,
            "row_factory": dict_row,
            # The Supabase session pooler doesn't keep prepared statements
            # across clients; plain statements work everywhere.
            "prepare_threshold": None,
            # Naive timestamp strings (datetime.utcnow().isoformat()) are
            # UTC, as they were under Supabase.
            "options": "-c timezone=UTC",
        },
        name=f"revise-{target}",
        open=True,
    )


def get_pool() -> ConnectionPool:
    global _pool, _pool_target
    if _pool is None:
        with _lock:
            if _pool is None:
                import cutover

                target = cutover.current_target()
                _pool = _open_pool(target)
                _pool_target = target
    return _pool


def current_pool_target() -> str | None:
    return _pool_target


def switch_target(target: str) -> None:
    """Point the process at another database. Only called by the cutover
    watcher while requests are held and none are in flight."""
    global _pool, _pool_target
    with _lock:
        if _pool_target == target and _pool is not None:
            return
        new_pool = _open_pool(target)
        old, _pool, _pool_target = _pool, new_pool, target
    if old is not None:
        old.close()


def close() -> None:
    global _pool, _pool_target
    with _lock:
        if _pool is not None:
            _pool.close()
        _pool, _pool_target = None, None


# --- value shaping -----------------------------------------------------------


def _iso_timestamp(dt: datetime) -> str:
    """Postgres' JSON rendering of timestamptz: trailing zeros trimmed from
    the fraction, e.g. 2026-03-08T21:40:05.5+00:00."""
    base = dt.replace(microsecond=0).isoformat()
    if not dt.microsecond:
        return base
    frac = f"{dt.microsecond:06d}".rstrip("0")
    return f"{base[:19]}.{frac}{base[19:]}"


def to_api(value):
    # Postgres renders a whole-number float8 as 3, not 3.0 (json.dumps's form).
    if isinstance(value, float) and value.is_integer() and abs(value) < 1e15:
        return int(value)
    if isinstance(value, datetime):
        return _iso_timestamp(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


def _shape(row: dict | None) -> dict | None:
    if row is None:
        return None
    return {k: to_api(v) for k, v in row.items()}


def _param(value):
    return Jsonb(value) if isinstance(value, (dict, list)) else value


# --- query helpers -----------------------------------------------------------


def fetch_all(query, params=None) -> list[dict]:
    with get_pool().connection() as conn:
        rows = conn.execute(query, params).fetchall()
    return [_shape(r) for r in rows]


def fetch_one(query, params=None) -> dict | None:
    with get_pool().connection() as conn:
        row = conn.execute(query, params).fetchone()
    return _shape(row)


def execute(query, params=None) -> int:
    """Run a statement and return the number of rows it touched."""
    with get_pool().connection() as conn:
        return conn.execute(query, params).rowcount


def _columns(cols: str) -> sql.Composable:
    """A trusted, comma-separated column list ("id, user_id, ...") as
    quoted identifiers."""
    return sql.SQL(", ").join(sql.Identifier(c.strip()) for c in cols.split(","))


def insert(table: str, data: dict, returning: str = "*") -> dict:
    keys = list(data)
    query = sql.SQL("INSERT INTO {} ({}) VALUES ({}) RETURNING {}").format(
        sql.Identifier(table),
        sql.SQL(", ").join(map(sql.Identifier, keys)),
        sql.SQL(", ").join(sql.Placeholder() * len(keys)),
        sql.SQL("*") if returning == "*" else _columns(returning),
    )
    return fetch_one(query, [_param(data[k]) for k in keys])


def upsert(
    table: str,
    data: dict,
    conflict: tuple[str, ...],
    ignore_duplicates: bool = False,
) -> dict | None:
    """INSERT ... ON CONFLICT, updating the given columns (or leaving the
    existing row alone). Returns the stored row, or None if ignored."""
    keys = list(data)
    updates = [k for k in keys if k not in conflict]
    if ignore_duplicates or not updates:
        action = sql.SQL("DO NOTHING")
    else:
        action = sql.SQL("DO UPDATE SET {}").format(
            sql.SQL(", ").join(
                sql.SQL("{} = EXCLUDED.{}").format(sql.Identifier(k), sql.Identifier(k))
                for k in updates
            )
        )
    query = sql.SQL("INSERT INTO {} ({}) VALUES ({}) ON CONFLICT ({}) {} RETURNING *").format(
        sql.Identifier(table),
        sql.SQL(", ").join(map(sql.Identifier, keys)),
        sql.SQL(", ").join(sql.Placeholder() * len(keys)),
        sql.SQL(", ").join(map(sql.Identifier, conflict)),
        action,
    )
    return fetch_one(query, [_param(data[k]) for k in keys])


def update(table: str, data: dict, where: dict, returning: str = "*") -> list[dict]:
    """UPDATE table SET data WHERE every where-column equals its value."""
    set_keys, where_keys = list(data), list(where)
    query = sql.SQL("UPDATE {} SET {} WHERE {} RETURNING {}").format(
        sql.Identifier(table),
        sql.SQL(", ").join(
            sql.SQL("{} = %s").format(sql.Identifier(k)) for k in set_keys
        ),
        sql.SQL(" AND ").join(
            sql.SQL("{} = %s").format(sql.Identifier(k)) for k in where_keys
        ),
        sql.SQL("*") if returning == "*" else _columns(returning),
    )
    params = [_param(data[k]) for k in set_keys] + [where[k] for k in where_keys]
    return fetch_all(query, params)


def delete(table: str, where: dict) -> list[dict]:
    where_keys = list(where)
    query = sql.SQL("DELETE FROM {} WHERE {} RETURNING *").format(
        sql.Identifier(table),
        sql.SQL(" AND ").join(
            sql.SQL("{} = %s").format(sql.Identifier(k)) for k in where_keys
        ),
    )
    return fetch_all(query, [where[k] for k in where_keys])
