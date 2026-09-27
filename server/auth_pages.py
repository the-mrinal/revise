"""Small server-rendered pages for the sign-in flows (GitHub, magic link)."""

import html
import json

_STYLE = """
body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
background:#0d0d10;color:#e8e8ea;font:15px/1.55 system-ui,-apple-system,sans-serif;padding:24px;box-sizing:border-box}
.box{background:#16161b;border:1px solid #2a2a33;border-radius:12px;padding:32px;max-width:440px;width:100%}
h1{font-size:20px;margin:0 0 10px}p{color:#a0a3ad;margin:0 0 14px}
a,button{display:block;width:100%;box-sizing:border-box;text-align:center;padding:11px;border-radius:8px;
font:600 14px system-ui,sans-serif;text-decoration:none;cursor:pointer;margin-top:10px}
.primary{background:#5b6abf;color:#fff;border:0}.secondary{background:none;color:#c8cad3;border:1px solid #2a2a33}
button:disabled{opacity:.5;cursor:not-allowed}.note{font-size:13px;color:#8a8f98}
"""


def page(title: str, body_html: str, status_code: int = 200):
    from fastapi.responses import HTMLResponse

    return HTMLResponse(
        f"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>{html.escape(title)} · Revise</title><style>{_STYLE}</style>"
        f"<div class=box><h1>{html.escape(title)}</h1>{body_html}</div>",
        status_code=status_code,
    )


def message(title: str, text: str, status_code: int = 400):
    return page(title, f"<p>{html.escape(text)}</p><a class=primary href='/dashboard'>Back to Revise</a>",
                status_code)


def signed_in(tokens: dict, next_path: str):
    """Store the session where the dashboard (and, through its content
    script, the extension) looks for it, then move on with a clean URL."""
    from fastapi.responses import HTMLResponse

    stored = json.dumps({"access_token": tokens["access_token"], "refresh_token": tokens["refresh_token"]})
    return HTMLResponse(
        "<script>localStorage.setItem('auth', " + json.dumps(stored).replace("</", "<\\/") + ");"
        " location.replace(" + json.dumps(next_path).replace("</", "<\\/") + ");</script>"
    )


def signed_in_back_to_sunday(tokens: dict, next_url: str):
    """Like signed_in, but the next page is on Sunday, where the extension's
    content script (capture-tokens.js, on this site only) can't follow. The
    session is stored by the first script on the page, before the content
    script's first read of localStorage can land; the page then stays about a
    second so that read and its copy into the extension finish, and goes on."""
    from fastapi.responses import HTMLResponse

    stored = json.dumps({"access_token": tokens["access_token"], "refresh_token": tokens["refresh_token"]})
    target = json.dumps(next_url).replace("</", "<\\/")
    return HTMLResponse(
        "<!doctype html><meta charset=utf-8>"
        "<script>localStorage.setItem('auth', " + json.dumps(stored).replace("</", "<\\/") + ");"
        " setTimeout(function () { location.replace(" + target + "); }, 1000);</script>"
        "<meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>Signed in · Revise</title><style>{_STYLE}</style>"
        "<div class=box><p>Revise</p><h1>Signed in. Taking you back to Sunday…</h1>"
        f"<a class=secondary href='{html.escape(next_url)}'>Continue</a></div>"
    )


def safe_next(path: str | None) -> str:
    """Only same-site paths, so the sign-in flow can't redirect elsewhere."""
    if path and path.startswith("/") and not path.startswith(("//", "/\\")):
        return path
    return "/dashboard"


def with_param(path: str, key: str, value: str) -> str:
    return f"{path}{'&' if '?' in path else '?'}{key}={value}"
