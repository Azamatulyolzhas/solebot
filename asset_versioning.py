"""Cache-busting for locally-served JS/CSS.

Browsers cache /dashboard/static/app.js aggressively (Opera especially).
Without a per-deploy version stamp on the URL, owners and customers
keep seeing yesterday's UI after we push a fix.

BUILD_ID is computed once at import:
  1. git short HEAD hash if the runtime has git + a repo (best — stable
     per-commit, identical across multiple workers)
  2. process start timestamp as a fallback (still busts cache per restart)

add_cache_bust(html) rewrites src="/foo.js" and href="/foo.css" to
src="/foo.js?v=<BUILD_ID>". External URLs (https://...) and already-
versioned URLs are left alone.
"""
import logging
import re
import subprocess
import time

log = logging.getLogger(__name__)


def _compute_build_id() -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
            timeout=2,
        )
        sha = out.decode().strip()
        if sha:
            return sha
    except Exception:
        pass
    return str(int(time.time()))


BUILD_ID: str = _compute_build_id()

# Match src="/path.js" or href="/path.css", optionally followed by ?query.
# Only local-rooted paths (start with /), external URLs (containing ://) skipped at runtime.
_ASSET_RE = re.compile(r'\b(src|href)="(/[^"]+?\.(?:js|css)(?:\?[^"]*)?)"')


def add_cache_bust(html: str, build_id: str | None = None) -> str:
    """Append ?v=<BUILD_ID> to every local JS/CSS URL in the HTML.

    Skips:
      - external URLs (no leading /, or contains ://)
      - URLs that already carry ?v=
    """
    bid = build_id or BUILD_ID
    if not bid:
        return html

    def _repl(m: re.Match) -> str:
        attr, url = m.group(1), m.group(2)
        if "?v=" in url or "://" in url:
            return m.group(0)
        sep = "&" if "?" in url else "?"
        return f'{attr}="{url}{sep}v={bid}"'

    return _ASSET_RE.sub(_repl, html)
