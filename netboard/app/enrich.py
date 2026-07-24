"""Reichert Dienste an: HTTP-Abruf, Seitentitel, korrektes Schema, Favicon.

Favicons werden serverseitig geholt und zwischengespeichert. Das ist wichtig:
viele Geräte sprechen HTTPS mit selbst signiertem Zertifikat – ein direkter
<img>-Abruf aus dem Browser würde blockiert oder eine Warnung erzeugen.
"""
from __future__ import annotations

import asyncio
import time
import re
from typing import Any
from urllib.parse import urljoin

import httpx

from .scanner import Device, Service

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_ICON_RE = re.compile(
    r"""<link\b[^>]*\brel\s*=\s*["']([^"']*\bicon\b[^"']*)["'][^>]*>""",
    re.IGNORECASE)
_HREF_RE = re.compile(r"""\bhref\s*=\s*["']([^"']+)["']""", re.IGNORECASE)

MAX_ICON = 200_000  # 200 kB reichen für jedes Favicon

# Cache: "ip:port" -> (content_type, bytes)
ICONS: dict[str, tuple[str, bytes]] = {}


# Titel, die nichts über den Dienst sagen. Bei denen greift lieber der
# bekannte Portname als „Login“ zehnmal untereinander.
_GENERIC = {
    "login", "log in", "sign in", "signin", "anmelden", "anmeldung",
    "dashboard", "home", "start", "startseite", "index", "welcome",
    "willkommen", "hauptseite", "main", "overview", "übersicht", "portal",
    "web interface", "webinterface", "console", "control panel", "admin",
    "administration", "management", "loading", "wird geladen", "untitled",
    "document", "app", "server", "router", "redirecting", "weiterleitung",
    "401 authorization required", "unauthorized", "authentication required",
    # Titel von Seiten, die noch laden
    "opening...", "opening", "loading...", "wird geladen...", "please wait",
    "bitte warten", "bitte warten...", "redirect", "connecting...",
    "initializing", "wird gestartet", "web management", "webmanagement",
    "router", "modem", "gateway", "access point", "switch", "nas", "printer",
    "drucker", "webserver", "web server", "apache", "nginx", "iis",
    "it works", "it works!", "test page", "default page", "new tab",
    # HTTP-Fehlerseiten sind keine Dienstnamen
    "bad request", "400 bad request", "forbidden", "403 forbidden",
    "not found", "404 not found", "page not found", "error",
    "internal server error", "500 internal server error", "bad gateway",
    "502 bad gateway", "service unavailable", "503 service unavailable",
    "gateway timeout", "504 gateway timeout",
}
# Zierrat, den viele Anmeldeseiten vorn oder hinten anhängen.
_STRIP_PRE = re.compile(
    r"^(?:login|log\s*in|sign\s*in|anmelden|anmeldung)\s*(?:[-–—:|·»]|bei|to|at)\s*",
    re.IGNORECASE)
_STRIP_POST = re.compile(
    r"\s*[-–—:|·«]\s*(?:login|log\s*in|sign\s*in|anmelden|anmeldung|dashboard|"
    r"home|startseite|admin|管理)\s*$", re.IGNORECASE)


# Viele Anmeldeseiten bauen ihren Titel per JavaScript. Im Rohtext steht dann
# ein Stück Quelltext – „" + ID_VC_Welcome + "“ ist ESXi, „{{ ... }}“ Angular.
# Sowas als Gerätenamen anzuzeigen ist schlimmer als gar kein Name.
_JUNK_BITS = ('" +', '+ "', "' +", "+ '", "{{", "}}", "${", "<%", "%>",
              "undefined", "null", "[object", "javascript:", "function(")
_IDENT = re.compile(r"^[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+){2,}$")


def _is_junk(title: str) -> bool:
    low = title.lower()
    if any(bit in low for bit in _JUNK_BITS):
        return True
    # Bezeichner wie ID_VC_Welcome – Quelltext, kein Name
    if _IDENT.match(title):
        return True
    # Fängt mit Anführungszeichen an und hat keins am Ende: abgeschnittener Code
    if title[:1] in "\"'" and title[-1:] not in "\"'":
        return True
    # Nur Satzzeichen
    if not re.search(r"[A-Za-zÄÖÜäöü0-9]", title):
        return True
    return False


def _clean_title(html: str) -> str | None:
    """Holt den Seitentitel und macht daraus, so gut es geht, einen Dienstnamen."""
    m = _TITLE_RE.search(html)
    if not m:
        return None
    title = re.sub(r"\s+", " ", m.group(1)).strip()
    # HTML-Entities, die in Titeln ständig vorkommen
    for a, b in (("&amp;", "&"), ("&quot;", '"'), ("&#39;", "'"),
                 ("&lt;", "<"), ("&gt;", ">"), ("&nbsp;", " ")):
        title = title.replace(a, b)
    title = title.strip()

    # „Login - Portainer“ -> „Portainer“, „Nextcloud | Login“ -> „Nextcloud“
    for _ in range(2):
        before = title
        title = _STRIP_PRE.sub("", title)
        title = _STRIP_POST.sub("", title)
        title = title.strip(" -–—:|·»«")
        if title == before:
            break

    if not title or title.lower() in _GENERIC:
        return None
    # Fehlerseiten mit Code, z. B. „Bad Request (400)“ oder „400 – Fehler“
    low = title.lower()
    if re.match(r"^\d{3}\b", low) or re.search(
            r"\b(bad request|forbidden|not found|unauthorized|"
            r"internal server error|bad gateway|service unavailable)\b", low):
        return None
    # Reine Adressen oder Zahlen sagen auch nichts
    if re.fullmatch(r"[\d.:\s/]+", title):
        return None
    if _is_junk(title):
        return None
    return title[:60]


def _icon_candidates(html: str, base: str) -> list[str]:
    """Icon-Links aus dem HTML, beste zuerst; /favicon.ico als Rückfallebene."""
    found: list[tuple[int, str]] = []
    for tag_match in _ICON_RE.finditer(html):
        tag = tag_match.group(0)
        rel = tag_match.group(1).lower()
        href = _HREF_RE.search(tag)
        if not href:
            continue
        # apple-touch-icon ist meist größer und sauberer als das klassische .ico
        rank = 0 if "apple-touch" in rel else 1
        found.append((rank, urljoin(base, href.group(1))))
    found.sort(key=lambda x: x[0])
    urls = [u for _, u in found]
    urls.append(urljoin(base, "/favicon.ico"))
    seen, out = set(), []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


async def _fetch_icon(client: httpx.AsyncClient, urls: list[str],
                      key: str, timeout: float,
                      host_header: str | None = None,
                      connect_host: str | None = None) -> bool:
    for url in urls[:3]:
        headers = {}
        # Icon liegt auf demselben (eigenen) Host -> erwartete Adresse mitschicken.
        if host_header and connect_host and f"//{connect_host}:" in url:
            headers["Host"] = host_header
        try:
            r = await client.get(url, timeout=timeout, follow_redirects=True,
                                  headers=headers)
        except (httpx.HTTPError, OSError):
            continue
        if r.status_code != 200 or not r.content:
            continue
        ctype = r.headers.get("content-type", "").split(";")[0].strip().lower()
        looks_image = ctype.startswith("image/") or "icon" in ctype
        if not looks_image or len(r.content) > MAX_ICON:
            continue
        ICONS[key] = (ctype or "image/x-icon", r.content)
        return True
    return False


async def _probe(client: httpx.AsyncClient, ip: str, svc: Service,
                 sem: asyncio.Semaphore, cfg: dict[str, Any],
                 connect_ip: str | None = None) -> None:
    timeout = cfg.get("http_timeout", 3.0)
    want_icons = cfg.get("favicons", True)
    host = connect_ip or ip     # eigener Host wird über 127.0.0.1 erreicht
    # Der Dienst soll die erwartete Adresse sehen (z. B. Django/paperless prüfen
    # ALLOWED_HOSTS und antworten sonst mit „400 Bad Request“).
    host_header = f"{ip}:{svc.port}"
    # Bevorzugtes Schema zuerst, dann das andere als Rückfallebene
    order = [svc.scheme, "http" if svc.scheme == "https" else "https"]

    async with sem:
        for scheme in order:
            base = f"{scheme}://{host}:{svc.port}"
            t0 = time.perf_counter()
            try:
                r = await client.get(base + "/", timeout=timeout,
                                     follow_redirects=True,
                                     headers={"Host": host_header})
            except (httpx.HTTPError, OSError):
                continue

            # Antwortzeit merken – gemessen bis zur fertigen Antwort.
            svc.ms = max(1, int((time.perf_counter() - t0) * 1000))
            svc.ok = True
            svc.scheme = scheme
            html = ""
            # Titel/Icon nur aus einer echten Erfolgsantwort ziehen – eine
            # Fehlerseite („400 Bad Request“, „401 …“) ist kein Dienstname.
            if r.status_code < 400 and "text/html" in r.headers.get("content-type", ""):
                html = r.text
                svc.title = _clean_title(html)

            if want_icons and r.status_code < 400:
                key = f"{ip}:{svc.port}"
                svc.icon = await _fetch_icon(
                    client, _icon_candidates(html, base), key, timeout,
                    host_header, host)
            return


# Favicons für Schnelllinks: einmal geholt, dann zwischengespeichert.
_FAV: dict[str, tuple[str, bytes]] = {}


async def site_favicon(url: str) -> tuple[str, bytes] | None:
    """Favicon einer beliebigen Seite serverseitig holen. Serverseitig, damit
    auch LAN-Dienste mit selbst signiertem Zertifikat funktionieren."""
    if not url or not re.match(r"^https?://", url):
        return None
    if url in _FAV:
        return _FAV[url]
    from urllib.parse import urlsplit
    parts = urlsplit(url)
    base = f"{parts.scheme}://{parts.netloc}"
    async with httpx.AsyncClient(verify=False,
                                 headers={"User-Agent": "Netboard/2.0"}) as client:
        html = ""
        try:
            r = await client.get(base + "/", timeout=4.0, follow_redirects=True)
            if "text/html" in r.headers.get("content-type", ""):
                html = r.text
        except (httpx.HTTPError, OSError):
            pass
        for u in _icon_candidates(html, base)[:4]:
            try:
                r = await client.get(u, timeout=4.0, follow_redirects=True)
            except (httpx.HTTPError, OSError):
                continue
            if r.status_code != 200 or not r.content:
                continue
            ctype = r.headers.get("content-type", "").split(";")[0].strip().lower()
            if (ctype.startswith("image/") or "icon" in ctype) and len(r.content) <= MAX_ICON:
                _FAV[url] = (ctype or "image/x-icon", r.content)
                return _FAV[url]
    return None


async def enrich(devices: list[Device], cfg: dict[str, Any]) -> list[Device]:
    sem = asyncio.Semaphore(int(cfg.get("concurrency", 40)))
    from . import scanner
    mine = scanner.local_ips()      # eigene Dienste über localhost prüfen
    # Zertifikate im LAN sind fast immer selbst signiert -> Prüfung aus.
    async with httpx.AsyncClient(verify=False,
                                 headers={"User-Agent": "Netboard/2.0"}) as client:
        tasks = [_probe(client, d.ip, s, sem, cfg,
                        "127.0.0.1" if d.ip in mine else None)
                 for d in devices for s in d.services]
        if tasks:
            await asyncio.gather(*tasks)

    # Cache aufräumen: Icons verschwundener Geräte rauswerfen
    live = {f"{d.ip}:{s.port}" for d in devices for s in d.services}
    for stale in set(ICONS) - live:
        ICONS.pop(stale, None)
    return devices
