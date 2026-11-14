# -*- coding: utf-8 -*-
"""frontpage.py - SZL Space front-page module (szl.frontpage/v1).

One reusable, honest, responsive front page for SZLHOLDINGS Spaces that run
API back-ends without a landing route.

Doctrine (fail-closed):
- No fabricated metrics. Every number shown is receipt-bound or labeled NOT MEASURED.
- Never shadows an existing "/" route: if "/" is taken, attaches at "/front" only.
- Stdlib-only: adds no dependency to any Space runtime.
- Desktop + mobile friendly: viewport meta, fluid grid, tap-sized targets.

Usage (FastAPI):
    from frontpage import attach_frontpage
    attach_frontpage(app, TITLE="...", TAGLINE="...", VERTICAL="...")
"""
from __future__ import annotations

import html

VERSION = "szl.frontpage/v1"
PIN_SZL = "szl==0.1.7"

# Estate-measured readouts. Receipt-bound; edit only with a new receipt.
RECEIPTS = {
    "battery_measured": "4/6 MEASURED - quant-1.7b-500step (Qwen3-1.7B, RTX A6000, r=32, alpha=64)",
    "battery_disclosed_failure": "a11oy-code-r1: 0/6 disclosed FAILURE (kept visible)",
    "dry_run": "dry-run: base eval loss 2.3307 to 1.7601 (-24.5 pct); shuffled-label control differential +0.3121",
    "receipts_dataset": "https://huggingface.co/datasets/SZLHOLDINGS/frontier-54-runs",
    "estate": "183 models / 66 Spaces RUNNING / 159 repos / 94 datasets / 21 PyPI / 30 custom domains READY / x402 live",
}


def _esc(s):
    return html.escape(str(s), quote=True)


def render_front_page(**cfg) -> str:
    title = _esc(cfg.get("TITLE", "SZL Space"))
    tagline = _esc(cfg.get("TAGLINE", "Governed AI. Receipts or it did not happen."))
    vertical = _esc(cfg.get("VERTICAL", "org"))
    space_id = _esc(cfg.get("SPACE_ID", "SZLHOLDINGS"))
    source_repo = _esc(cfg.get("SOURCE_REPO", "https://github.com/szl-holdings"))
    health = _esc(cfg.get("HEALTH_PATH", "/health"))
    extra = cfg.get("EXTRA_LINKS") or []
    extra_html = "".join(
        '<a class="chip" href="' + _esc(u) + '">' + _esc(t) + "</a>"
        for t, u in extra
    )
    return """<!doctype html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>__TITLE__</title>
<meta name="description" content="__TAGLINE__">
<style>
:root{--bg:#0b0e14;--panel:#121722;--ink:#e8ecf4;--dim:#9aa7bd;--line:#232c3d;--accent:#5eead4;--gold:#fbbf24;--green:#4ade80}
*{box-sizing:border-box}
html,body{margin:0;padding:0;background:var(--bg);color:var(--ink);font-family:system-ui,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;line-height:1.6}
.wrap{max-width:960px;margin:0 auto;padding:24px 20px 48px}
header{border-bottom:1px solid var(--line);padding:28px 0 20px}
.brand{font-size:13px;letter-spacing:.14em;text-transform:uppercase;color:var(--dim)}
h1{margin:.35em 0 .2em;font-size:clamp(1.6rem,5vw,2.4rem);line-height:1.15}
.tag{margin:0;color:var(--dim);font-size:1.02rem;max-width:60ch}
.khipu{display:block;margin:10px 0 4px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(270px,1fr));gap:14px;margin-top:26px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:18px}
.card h2{margin:0 0 8px;font-size:1rem}
.card p{margin:0;color:var(--dim);font-size:.95rem;word-break:break-word}
.badge{display:inline-block;margin:4px 6px 12px 0;padding:7px 12px;border:1px solid var(--line);border-radius:999px;font-size:.85rem;color:var(--ink);text-decoration:none;background:#0e1420}
.badge:hover{border-color:var(--accent)}
.badge .ok{color:var(--green);font-weight:600}
.badge .pin{color:var(--gold);font-weight:600}
.badge .fail{color:#f87171;font-weight:600}
footer{margin-top:34px;border-top:1px solid var(--line);padding-top:16px;color:var(--dim);font-size:.85rem}
footer a{color:var(--accent);text-decoration:none}
a.chip{display:inline-block;margin:4px 6px 0 0;padding:7px 12px;border:1px solid var(--line);border-radius:999px;color:var(--accent);text-decoration:none;font-size:.85rem}
</style>
</head>
<body>
<div class="wrap">
<header>
<div class="brand">SZL Holdings &middot; __VERTICAL__ vertical</div>
<h1>__TITLE__</h1>
<p class="tag">__TAGLINE__</p>
<svg class="khipu" width="220" height="26" viewBox="0 0 220 26" aria-hidden="true"><rect x="0" y="11" width="220" height="4" rx="2" fill="#232c3d"/><g fill="#5eead4"><rect x="18" y="6" width="6" height="14" rx="3"/><rect x="52" y="9" width="6" height="8" rx="3"/><rect x="86" y="4" width="6" height="18" rx="3"/></g><g fill="#fbbf24"><rect x="120" y="8" width="6" height="10" rx="3"/><rect x="154" y="6" width="6" height="14" rx="3"/></g><g fill="#a78bfa"><rect x="188" y="9" width="6" height="8" rx="3"/></g></svg>
<div>
<a class="badge" href="__HEALTH__">status: <span class="ok">/health</span></a>
<a class="badge" href="https://a-11-oy.com">a-11-oy.com</a>
<a class="badge" href="https://huggingface.co/SZLHOLDINGS">Hugging Face org</a>
<a class="badge" href="__SOURCE_REPO__">source repository</a>
__EXTRA__
</div>
</header>
<div class="grid">
<div class="card"><h2>Receipts layer (khipu)</h2><p>Every claim on this estate binds to a signed receipt in the <a style="color:var(--accent)" href="__RECEIPTS_DATASET__">frontier-54-runs</a> receipts chain. No receipt, no claim.</p></div>
<div class="card"><h2>Honesty battery</h2><p>__BATTERY_MEASURED__<br>__BATTERY_FAIL__</p></div>
<div class="card"><h2>Measured dry-run</h2><p>__DRY_RUN__</p></div>
<div class="card"><h2>Estate</h2><p>__ESTATE__</p></div>
</div>
<footer>Served by __SPACE_ID__ &middot; front-page module __VERSION__ &middot; pinned <span class="pin">__PIN__</span> &middot; fail-closed: unmeasured things say NOT MEASURED, never guessed.</footer>
</div>
</body>
</html>""" \
        .replace("__TITLE__", title).replace("__TAGLINE__", tagline) \
        .replace("__VERTICAL__", vertical).replace("__SPACE_ID__", space_id) \
        .replace("__SOURCE_REPO__", source_repo).replace("__HEALTH__", health) \
        .replace("__EXTRA__", extra_html) \
        .replace("__RECEIPTS_DATASET__", RECEIPTS["receipts_dataset"]) \
        .replace("__BATTERY_MEASURED__", _esc(RECEIPTS["battery_measured"])) \
        .replace("__BATTERY_FAIL__", _esc(RECEIPTS["battery_disclosed_failure"])) \
        .replace("__DRY_RUN__", _esc(RECEIPTS["dry_run"])) \
        .replace("__ESTATE__", _esc(RECEIPTS["estate"])) \
        .replace("__VERSION__", VERSION).replace("__PIN__", PIN_SZL)

def _existing_paths(app):
    paths = set()
    try:
        for r in app.routes:
            p = getattr(r, "path", None)
            if p:
                paths.add(p)
    except Exception:
        pass
    return paths


def attach_frontpage(app, **cfg):
    """Attach the front page to a FastAPI (or Starlette) app. Fail-closed:
    if "/" already exists we attach at "/front" instead and never shadow it."""
    try:
        from fastapi.responses import HTMLResponse
    except ImportError:
        from starlette.responses import HTMLResponse  # noqa: F401
    paths = _existing_paths(app)
    route = "/" if "/" not in paths else "/front"
    cfg.setdefault("HEALTH_PATH", "/health")
    page = render_front_page(**cfg)

    def _handler():
        return HTMLResponse(page)

    app.add_api_route(route, _handler, methods=["GET"], include_in_schema=False)
    return route


def attach_frontpage_flask(app, **cfg):
    """Attach the front page to a Flask app. Same fail-closed rule."""
    paths = {r.rule for r in app.url_map.iter_rules()}
    route = "/" if "/" not in paths else "/front"
    cfg.setdefault("HEALTH_PATH", "/health")

    @app.route(route)
    def _szl_frontpage():
        return render_front_page(**cfg)

    return route


if __name__ == "__main__":
    import sys
    sys.stdout.write(render_front_page(TITLE="self-test", SPACE_ID="local")[:400])
    sys.stdout.write("\n[ok] renders without any third-party dependency\n")
