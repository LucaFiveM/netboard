"""Version von Netboard und Nachschauen, ob es eine neuere gibt.

Die Prüfung fragt die öffentliche GitHub-API nach der jüngsten Veröffentlichung
(``/releases/latest``). Sie ist bewusst freiwillig: ohne hinterlegtes Repository
passiert gar nichts, und das Ergebnis wird zwischengespeichert, damit nicht bei
jedem Seitenaufruf ins Netz gegangen wird.
"""
from __future__ import annotations

import re
import time
from typing import Any

import httpx

VERSION = "1.0.0"
CACHE_SECONDS = 6 * 3600

_cache: dict[str, Any] = {"at": 0, "data": None}


def parse(v: str) -> tuple[int, int, int]:
    """„v1.2.3“ → (1, 2, 3). Unbekanntes wird zu (0, 0, 0)."""
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", str(v or ""))
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else (0, 0, 0)


def is_newer(remote: str, local: str = VERSION) -> bool:
    return parse(remote) > parse(local)


async def check(repo: str, force: bool = False) -> dict[str, Any]:
    """Nach einer neueren Fassung sehen. ``repo`` ist z. B. ``luca/netboard``."""
    repo = (repo or "").strip().strip("/")
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
        return {"ok": False, "version": VERSION,
                "error": "Kein gültiges GitHub-Repository hinterlegt."}
    now = time.time()
    if not force and _cache["data"] and now - _cache["at"] < CACHE_SECONDS:
        return _cache["data"]
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    try:
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
            r = await client.get(url, headers={"Accept": "application/vnd.github+json",
                                               "User-Agent": "Netboard"})
        if r.status_code == 404:
            out = {"ok": True, "version": VERSION, "latest": None,
                   "update": False, "note": "Noch keine Veröffentlichung im Repository."}
        elif r.status_code != 200:
            out = {"ok": False, "version": VERSION,
                   "error": f"GitHub antwortete mit {r.status_code}."}
        else:
            j = r.json()
            tag = str(j.get("tag_name") or j.get("name") or "")
            out = {"ok": True, "version": VERSION, "latest": tag,
                   "update": is_newer(tag), "url": j.get("html_url") or "",
                   "published": (j.get("published_at") or "")[:10],
                   "notes": (j.get("body") or "")[:1200]}
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        out = {"ok": False, "version": VERSION,
               "error": f"Nicht erreichbar ({type(exc).__name__})."}
    if out.get("ok"):
        _cache.update({"at": now, "data": out})
    return out
