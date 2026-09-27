"""Zero-downtime database cutover: hold requests, switch databases, release.

The running server and the operator talk through two small JSON files in
CUTOVER_DIR (a persistent volume):

  control.json  written by the operator: {"hold": bool, "target": "supabase"|"local"}
  status.json   written by the server:   {"hold", "inflight", "target", "updated_at", "pid"}

While "hold" is on, API requests wait (up to HOLD_MAX_SECONDS) instead of
failing, so a save made mid-cutover just takes a few seconds longer. The
server switches its connection pool to a new target only while held and
with no request in flight. control.json outlives restarts, so a server that
restarts after the cutover keeps using the new database even before .env
is edited.

The server must run as a single process (one uvicorn worker): the hold and
in-flight count live in memory.

Operator usage (inside the server container):
  python cutover.py status
  python cutover.py hold            # wait until nothing is in flight
  python cutover.py switch local    # requires hold; waits until switched
  python cutover.py release
"""

import asyncio
import json
import os
import sys
import threading
import time
from datetime import datetime, timezone

CUTOVER_DIR = os.environ.get("CUTOVER_DIR", "/data/cutover")
CONTROL = os.path.join(CUTOVER_DIR, "control.json")
STATUS = os.path.join(CUTOVER_DIR, "status.json")
HOLD_MAX_SECONDS = float(os.environ.get("HOLD_MAX_SECONDS", "60"))
POLL_SECONDS = 0.2

# Requests that never touch the database keep flowing during a hold. Sign-in
# and token refresh still go to Supabase Auth in this phase.
_UNHELD_PREFIXES = ("/api/auth/",)

_state = {"hold": False, "inflight": 0}


def _read_json(path: str) -> dict | None:
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _write_json(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp{os.getpid()}-{threading.get_ident()}"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def current_target() -> str:
    """The database that should serve requests: control.json wins over the
    DB_TARGET environment variable, which defaults to our own Postgres."""
    control = _read_json(CONTROL) or {}
    target = control.get("target") or os.environ.get("DB_TARGET", "local")
    if target not in ("local", "supabase"):
        raise RuntimeError(f"Unknown database target '{target}'")
    return target


# --- server side ---------------------------------------------------------------


def is_held_path(path: str) -> bool:
    return path.startswith("/api/") and not path.startswith(_UNHELD_PREFIXES)


async def gate(request, call_next):
    """HTTP middleware: wait out a hold, then count the request as in flight."""
    from fastapi.responses import JSONResponse

    if not is_held_path(request.url.path):
        return await call_next(request)
    waited = 0.0
    while _state["hold"]:
        if waited >= HOLD_MAX_SECONDS:
            return JSONResponse(
                {"detail": "Revise is finishing a quick upgrade. Please try again in a minute."},
                status_code=503,
                headers={"Retry-After": "30"},
            )
        await asyncio.sleep(0.1)
        waited += 0.1
    # No await between the check above and this increment, so a hold that
    # starts now will see this request in the in-flight count.
    _state["inflight"] += 1
    try:
        return await call_next(request)
    finally:
        _state["inflight"] -= 1


def _watch_once() -> None:
    import db

    control = _read_json(CONTROL) or {}
    _state["hold"] = bool(control.get("hold"))
    wanted = control.get("target")
    active = db.current_pool_target()
    if wanted and active and wanted != active and _state["hold"] and _state["inflight"] == 0:
        print(f"[cutover] switching database {active} -> {wanted}")
        db.switch_target(wanted)
        print(f"[cutover] now serving from {wanted}")
    _write_json(
        STATUS,
        {
            "hold": _state["hold"],
            "inflight": _state["inflight"],
            "target": db.current_pool_target() or current_target(),
            "updated_at": time.time(),
            "pid": os.getpid(),
        },
    )


_stop = threading.Event()


def start_watcher() -> threading.Thread | None:
    try:
        os.makedirs(CUTOVER_DIR, exist_ok=True)
    except OSError as e:
        print(f"[cutover] {CUTOVER_DIR} unavailable ({e}); cutover controls disabled")
        return None

    _stop.clear()

    def loop():
        while not _stop.is_set():
            try:
                _watch_once()
            except Exception as e:  # keep watching; never take the server down
                print(f"[cutover] watcher error: {e}")
            _stop.wait(POLL_SECONDS)

    t = threading.Thread(target=loop, name="cutover-watcher", daemon=True)
    t.start()
    return t


def stop_watcher() -> None:
    _stop.set()


# --- operator side ---------------------------------------------------------------


def _status(max_age: float = 3.0) -> dict:
    status = _read_json(STATUS)
    if not status or time.time() - status.get("updated_at", 0) > max_age:
        raise SystemExit("The server isn't reporting status. Is it running?")
    return status


def _wait(predicate, what: str, timeout: float = 30.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = _status()
        if predicate(status):
            return status
        time.sleep(POLL_SECONDS)
    raise SystemExit(f"Timed out waiting for {what}: {_status()}")


def _set_control(**changes) -> None:
    control = _read_json(CONTROL) or {"hold": False, "target": _status()["target"]}
    control.update(changes)
    control["changed_at"] = datetime.now(timezone.utc).isoformat()
    _write_json(CONTROL, control)


def hold() -> dict:
    """Start holding API requests; returns once nothing is in flight."""
    _set_control(hold=True)
    return _wait(lambda s: s["hold"] and s["inflight"] == 0, "requests to drain")


def switch(target: str) -> dict:
    """Move the server to another database. Requires an active hold."""
    if target not in ("local", "supabase"):
        raise SystemExit("target must be 'local' or 'supabase'")
    if not _status()["hold"]:
        raise SystemExit("Run 'hold' first.")
    _set_control(target=target)
    return _wait(lambda s: s["target"] == target, f"switch to {target}")


def release() -> dict:
    _set_control(hold=False)
    return _wait(lambda s: not s["hold"], "release")


def main(argv: list[str]) -> None:
    cmd = argv[1] if len(argv) > 1 else "status"
    if cmd == "status":
        print(json.dumps(_status(), indent=2))
    elif cmd == "hold":
        s = hold()
        print(f"Held. Nothing in flight. Serving from {s['target']}.")
    elif cmd == "switch" and len(argv) > 2:
        switch(argv[2])
        print(f"Switched. Serving from {argv[2]} (still held).")
    elif cmd == "release":
        s = release()
        print(f"Released. Serving from {s['target']}.")
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main(sys.argv)
