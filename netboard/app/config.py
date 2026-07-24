"""Einstellungen: persistent als JSON, komplett über die Weboberfläche pflegbar.

Alles liegt serverseitig. Wer heute vom Laptop und morgen vom PC kommt,
sieht denselben Stand – inklusive der Frage, ob die Einrichtung schon lief.

ENV-Variablen dienen nur als Startwerte beim allerersten Boot.
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
import threading
import uuid
from pathlib import Path
from typing import Any

DATA_DIR = Path(os.getenv("NETBOARD_DATA", "/data"))
CONF_FILE = DATA_DIR / "config.json"

DEFAULT_PORTS = (
    "80,443,81,88,591,2283,2480,3000,3001,4000,5000,5001,5055,7080,7878,8000,8006,"
    "8008,8010,8080,8081,8083,8085,8096,8112,8123,8181,8200,8384,8443,8686,8787,8888,"
    "8920,8989,9000,9090,9091,9117,9443,9696,9981,10000,32400,51821,61208"
)
QUICK_PORTS = "80,443,8080,8443,8006,8096,8123,9000,32400,51821"

# Ports, die in einer späteren Version dazukamen. Bestehende Configs bekommen sie
# einmalig ergänzt (Migration), damit z. B. wg-easy (51821) und paperless (8010)
# ohne Handarbeit gefunden werden. Wer einen Port danach entfernt, behält das.
PORTS_MIG = 1
PORTS_MIG_ADD = "2283,5055,8010,8112,51821,61208"

# Wie oft ein geöffnetes Dashboard nachprüft. 0 = nur beim Start und auf Knopfdruck.
REFRESH_CHOICES = (0, 30, 60, 300, 900, 3600)
LAYOUTS = ("grid", "cards", "list")
# Wie viel Rahmen eine Kachel trägt und wie viel Text darunter steht.
TILE_STYLES = ("outline", "soft", "bare")
LABEL_MODES = ("full", "name", "none")
# Wie breit ein Dashboard den Bildschirm nutzt.
WIDTHS = ("narrow", "normal", "wide", "full")
# Ordnung in einer Kachelwand: 35 Kacheln ohne Gliederung sind Chaos.
# „custom“ = selbst angelegte Ordner, deren Zuordnung am Dashboard hängt.
GROUPS = ("none", "device", "subnet", "custom")
SORTS = ("natural", "name", "device", "port")
ICON_SHAPES = ("rounded", "round", "square")
# Systemwerte oben in der Leiste.
STATS_SOURCES = ("off", "local", "vsphere")
STATS_STYLES = ("bars", "text", "dots")
THEMES = ("auto", "light", "dark")
ACCENTS = ("blau", "indigo", "gruen", "rot", "orange", "grau",
           "tuerkis", "cyan", "violett", "pink", "limette", "kupfer")
TILE_SIZES = ("compact", "normal", "large")
NAMING = ("known", "title")
NET_MODES = ("online", "offline")
SCHEME_FILTERS = ("all", "https", "http")
FONTS = ("system", "custom", "inter", "roboto", "opensans", "lato", "nunito",
         "poppins", "sourcesans", "plexsans", "manrope", "figtree")
# Hintergrund der Seite – rein dekorativ, per CSS, kein externer Abruf.
BACKGROUNDS = ("plain", "tint", "grid", "lines", "glow", "mesh", "vignette",
               "aurora", "sunset", "ocean", "dusk", "custom", "image")
# Wie mit gefundenen Geräten umgegangen wird.
#   auto   – alles Gefundene erscheint sofort (bisheriges Verhalten)
#   pinned – nur angeheftete (und eigene) Geräte werden gezeigt; der Rest
#            wird zwar gefunden, aber erst nach Auswahl aufgenommen
SCAN_MODES = ("auto", "pinned")

# Such-/Kommandoleiste: Bang-Provider. „%s“ wird durch die Eingabe ersetzt.
DEFAULT_PROVIDERS = [
    {"bang": "g",   "name": "Google",      "url": "https://www.google.com/search?q=%s"},
    {"bang": "ddg", "name": "DuckDuckGo",  "url": "https://duckduckgo.com/?q=%s"},
    {"bang": "yt",  "name": "YouTube",     "url": "https://www.youtube.com/results?search_query=%s"},
    {"bang": "gh",  "name": "GitHub",      "url": "https://github.com/search?q=%s"},
    {"bang": "w",   "name": "Wikipedia",   "url": "https://de.wikipedia.org/w/index.php?search=%s"},
]

DEFAULTS: dict[str, Any] = {
    # Optionaler Login. Standardmäßig aus. Das Passwort steht nur als
    # scrypt-Hash da – nicht wiederherstellbar, auch nicht durch uns.
    "auth": {"enabled": False, "username": "admin", "password_hash": ""},
    # Benutzerkonten. Werden nur über die /api/users-Endpunkte gepflegt, damit
    # Passwörter und 2FA-Geheimnisse nie über einen Import hereinkommen.
    "users": [],
    # Betriebsart: "online" darf ins Internet (Logos, Web-Schriften, Wetter,
    # Update-Prüfung), "offline" unterlässt jede Anfrage nach draußen.
    "net_mode": "online",
    "update_check": True,     # täglich bei GitHub nach einer neuen Fassung sehen
    "update_seen": "",        # ausgeblendete Fassung (Hinweis nicht erneut zeigen)
    # Hinweise je Benutzer (z. B. „X hat ein Dashboard mit dir geteilt“).
    # Werden wie Konten nur serverseitig gepflegt, nie über einen Import.
    "notices": {},
    "configured": False,     # steuert den Einrichtungsassistenten
    "subnets": [],           # z. B. ["192.168.178.0/24"]
    "ports": DEFAULT_PORTS,
    "_ports_mig": 0,           # Migrationsstand der Portliste (0 = noch nicht)
    "http_timeout": 3.0,
    "concurrency": 40,
    "favicons": True,
    "auto_icons": False,   # fehlende Logos automatisch aus dashboard-icons ziehen
    "resolve_names": True, # interne DNS-Namen (PTR) als Gerätenamen nutzen
    "show_ping": False,    # Antwortzeit als kleines Abzeichen auf der Kachel
    "auto_tint": False,    # Kacheln dezent in der Hintergrundfarbe tönen
    # Darstellung – reine Geschmacksfragen, greifen ohne neuen Suchlauf.
    "theme": "auto",          # auto | light | dark
    "accent": "blau",
    "tile_size": "normal",    # compact | normal | large
    "background": "plain",    # plain|tint|grid|lines|glow|mesh|vignette|aurora|sunset|ocean|dusk|custom|image
    # Eigener Farbverlauf (bei background=="custom").
    "bg_grad": {"a": "#3B82F6", "b": "#8B5CF6", "angle": 135},
    # Such-/Kommandoleiste. „!bang begriff“ springt zum Anbieter; ohne Treffer
    # im eigenen Netz sucht Enter beim Standard-Anbieter.
    "search_providers": [dict(p) for p in DEFAULT_PROVIDERS],
    "search_default": "ddg",  # Bang, der bei freier Eingabe + Enter greift
    # Wetter in der Kopfzeile. Standardmäßig aus – der einzige optionale Abruf
    # nach außen (open-meteo, ohne Schlüssel), und nur aus dem Browser.
    "weather": {"enabled": False, "lat": "", "lon": "", "label": ""},
    # Wie ein Dienst heißen soll: bekannter Name je Port, oder was die Seite
    # in ihrem Titel behauptet.
    "service_naming": "known",   # known | title
    "scheme_filter": "all",      # all | https | http
    "font": "system",            # Schriftart der Oberfläche
    "_fontv": 0,                 # Version der eigenen Schriftdatei
    "mono_custom": False,        # eigene dicktengleiche Schrift hinterlegt?
    # Systemwerte in der Kopfzeile
    "stats_source": "local",     # off | local | vsphere
    "stats_style": "bars",       # bars | text | dots
    "stats_show": ["cpu", "ram", "uptime"],
    # Zugang zu ESXi/vCenter. Das Passwort liegt im Klartext in der Datei –
    # deshalb: eigenes Konto, nur Lesen.
    "vsphere": {"host": "", "port": 443, "user": "", "password": "",
                "insecure": True, "target": "", "tiles": False},
    # Proxmox VE: Live-Werte je Node auf die passende Kachel (Token, nur lesen).
    "proxmox": {"enabled": False, "host": "", "token_id": "",
                "token_secret": "", "insecure": True},
    "aliases": {},           # {"192.168.178.103": "Balkon-Sensor"}
    "priority_ports": [],    # Dienste dieser Ports zuerst, in dieser Reihenfolge
    "hide_empty": False,     # Geräte ganz ohne Web-Dienst ausblenden
    "manual_devices": [],    # [{"id","name","ip","ports"}]
    # Angeheftete Geräte: bleiben auf dem Board, auch wenn sie offline gehen.
    # Wir merken uns den letzten Stand, um sie offline anzeigen zu können.
    "scan_mode": "auto",     # auto | pinned
    "pinned": [],            # [{"ip","mac","hostname","vendor","alias","ssh_port","ports"}]
    # SSH-Zugänge zum Auslesen von OS/Updates. Passwort verschlüsselt auf Platte.
    "probes": [],            # [{"ip","user","password","enabled"}]
    # Schnelllinks: frei angelegte Kacheln, die kein Gerät im Netz brauchen –
    # für Dinge außerhalb des LAN (Docs, Cloud) oder Deeplinks in einen Dienst.
    # [{"id","name","url","icon","color","boards"}] – boards leer = überall.
    "links": [],
    # Eigenes Aussehen einzelner Kacheln: Schlüssel = Kachel-Key (wie beim
    # Sortieren, z. B. "192.168.178.5:8006" oder "link:ab12").
    "tile_styles": {},       # {key: {"bg","grad","image","css"}}
    "hidden": [],            # überall deaktiviert – wird nicht mal abgefragt
    # Dashboards sind gespeicherte Sichten auf dieselben Scandaten.
    # [{"id","name","auto_add","refresh","layout","only_online","members","hidden"}]
    "dashboards": [],
    "default_dashboard": "",  # wird beim Laden zuerst gezeigt
    # SSH-Terminal im Browser. Standardmäßig aus – siehe README.
    "ssh_enabled": False,
    "ssh_ports": "22",
    "ssh_users": {},          # {"192.168.178.20": "root"} – nur Namen, nie Passwörter
}

_lock = threading.Lock()
_cache: dict[str, Any] | None = None


def _new_id() -> str:
    return uuid.uuid4().hex[:8]


# --- Einzelne Feldprüfungen --------------------------------------------------
def _clean_subnets(value: Any) -> tuple[list[str], list[str]]:
    if isinstance(value, str):
        value = [v for v in re.split(r"[,\s]+", value) if v]
    if not isinstance(value, list):
        return [], ["Netze müssen eine Liste sein."]
    out: list[str] = []
    errs: list[str] = []
    for raw in value:
        raw = str(raw).strip()
        if not raw:
            continue
        try:
            net = ipaddress.ip_network(raw, strict=False)
        except ValueError:
            errs.append(f"„{raw}“ ist kein gültiges Netz (z. B. 192.168.178.0/24).")
            continue
        if net.version != 4:
            errs.append(f"„{raw}“: nur IPv4 wird unterstützt.")
        elif net.prefixlen < 20:
            errs.append(f"„{raw}“ ist zu groß – /20 oder kleiner (max. 4096 Adressen).")
        elif str(net) not in out:
            out.append(str(net))
    return out, errs


def _clean_ports(value: Any) -> tuple[str, list[str]]:
    if isinstance(value, (list, tuple)):
        value = ",".join(str(v) for v in value)
    parts = [p for p in re.split(r"[,\s]+", str(value)) if p]
    out: list[str] = []
    errs: list[str] = []
    for p in parts:
        m = re.fullmatch(r"(\d{1,5})(?:-(\d{1,5}))?", p)
        if not m:
            errs.append(f"„{p}“ ist keine Portangabe (z. B. 80 oder 8000-8100).")
            continue
        nums = [int(x) for x in m.groups() if x]
        if any(not 1 <= n <= 65535 for n in nums):
            errs.append(f"„{p}“ liegt außerhalb von 1–65535.")
            continue
        if len(nums) == 2 and nums[0] > nums[1]:
            errs.append(f"„{p}“: Bereich läuft rückwärts.")
            continue
        if p not in out:
            out.append(p)
    if not out and not errs:
        errs.append("Mindestens ein Port muss angegeben sein.")
    return ",".join(out), errs


def _clean_ip_list(value: Any) -> tuple[list[str], list[str]]:
    if isinstance(value, str):
        value = [v for v in re.split(r"[,\s]+", value) if v]
    if not isinstance(value, list):
        return [], ["Liste von IP-Adressen erwartet."]
    out: list[str] = []
    errs: list[str] = []
    for raw in value:
        ip = str(raw).strip()
        if not ip:
            continue
        try:
            ipaddress.ip_address(ip)
        except ValueError:
            errs.append(f"„{ip}“ ist keine gültige IP-Adresse.")
            continue
        if ip not in out:
            out.append(ip)
    return out, errs


def _clean_alias_map(value: Any) -> tuple[dict[str, str], list[str]]:
    if not isinstance(value, dict):
        return {}, ["Wunschnamen müssen als Zuordnung IP → Name kommen."]
    out: dict[str, str] = {}
    errs: list[str] = []
    for ip, name in value.items():
        ip = str(ip).strip()
        name = str(name).strip()[:48]
        if not name:
            continue  # leerer Name = Alias entfernen
        try:
            ipaddress.ip_address(ip)
        except ValueError:
            errs.append(f"„{ip}“ ist keine gültige IP-Adresse.")
            continue
        out[ip] = name
    return out, errs


def _clean_manual(value: Any) -> tuple[list[dict[str, str]], list[str]]:
    if not isinstance(value, list):
        return [], ["Eigene Geräte müssen als Liste kommen."]
    out: list[dict[str, str]] = []
    errs: list[str] = []
    seen: set[str] = set()
    for i, raw in enumerate(value, 1):
        if not isinstance(raw, dict):
            errs.append(f"Eigenes Gerät {i}: unerwartetes Format.")
            continue
        ip = str(raw.get("ip", "")).strip()
        try:
            ipaddress.ip_address(ip)
        except ValueError:
            errs.append(f"Eigenes Gerät {i}: „{ip}“ ist keine gültige IP-Adresse.")
            continue
        if ip in seen:
            errs.append(f"„{ip}“ ist mehrfach als eigenes Gerät angelegt.")
            continue
        ports, perr = _clean_ports(raw.get("ports", ""))
        if perr:
            errs += [f"Eigenes Gerät {ip}: {e}" for e in perr]
            continue
        seen.add(ip)
        out.append({"id": str(raw.get("id", "")).strip()[:16] or _new_id(),
                    "name": str(raw.get("name", "")).strip()[:48],
                    "ip": ip, "ports": ports})
    return out, errs


def _clean_url(raw: Any) -> str:
    """Aus einer Nutzereingabe eine brauchbare URL machen. Fehlt das Schema,
    ergänzen wir https:// – niemand tippt gern „https://“ vor jede Adresse."""
    s = str(raw or "").strip()
    if not s:
        return ""
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", s):
        s = "https://" + s
    return s[:400]


def _clean_tile_image(raw: Any) -> str:
    """Kachel-Bild: entweder eine hochgeladene Data-URL (verkleinert, daher klein)
    oder eine normale Bild-URL."""
    s = str(raw or "").strip()
    if not s:
        return ""
    if s.startswith("data:image/"):
        # Zu große Bilder lieber ganz verwerfen: ein abgeschnittener Data-URL
        # ergäbe ein kaputtes Bild, das niemand erklären kann.
        return s if len(s) <= 600000 else ""
    return _clean_url(s)


def _clean_providers(value: Any) -> tuple[list[dict[str, str]], list[str]]:
    """Such-Anbieter: Kürzel (bang), Name und URL-Vorlage mit „%s“."""
    if not isinstance(value, list):
        return [], ["Such-Anbieter müssen als Liste kommen."]
    out: list[dict[str, str]] = []
    errs: list[str] = []
    seen: set[str] = set()
    for i, raw in enumerate(value, 1):
        if not isinstance(raw, dict):
            errs.append(f"Anbieter {i}: unerwartetes Format.")
            continue
        bang = re.sub(r"[^A-Za-z0-9]", "", str(raw.get("bang", "")).strip().lstrip("!"))[:8].lower()
        name = str(raw.get("name", "")).strip()[:24]
        url = _clean_url(raw.get("url", ""))
        if not bang:
            errs.append(f"Anbieter {i}: Kürzel fehlt.")
            continue
        if "%s" not in url:
            errs.append(f"„{bang}“: Adresse braucht ein %s für den Suchbegriff.")
            continue
        if bang in seen:
            errs.append(f"Kürzel „{bang}“ ist doppelt.")
            continue
        seen.add(bang)
        out.append({"bang": bang, "name": name or bang, "url": url})
    return out, errs


def _clean_weather(value: Any, cur: dict | None = None) -> tuple[dict[str, Any], list[str]]:
    if not isinstance(value, dict):
        return {}, ["Wetter: unerwartetes Format."]
    cur = cur or {}
    errs: list[str] = []

    def _coord(key: str, lo: float, hi: float) -> str:
        v = str(value.get(key, cur.get(key, ""))).strip().replace(",", ".")
        if v == "":
            return ""
        try:
            f = float(v)
        except ValueError:
            errs.append(f"Wetter: „{v}“ ist keine Zahl.")
            return ""
        if not lo <= f <= hi:
            errs.append(f"Wetter: Wert außerhalb {lo}…{hi}.")
            return ""
        return f"{f:.4f}".rstrip("0").rstrip(".")

    return {
        "enabled": bool(value.get("enabled", cur.get("enabled", False))),
        "lat": _coord("lat", -90, 90),
        "lon": _coord("lon", -180, 180),
        "label": str(value.get("label", cur.get("label", ""))).strip()[:32],
    }, errs


def _valid_ip(ip: str) -> bool:
    try:
        ipaddress.ip_address(ip)
        return True
    except ValueError:
        return False


def _clean_probes(value: Any) -> tuple[list[dict[str, Any]], list[str]]:
    """SSH-Zugänge je Gerät. Leeres Passwort heißt: vorhandenes behalten."""
    if not isinstance(value, list):
        return [], ["SSH-Zugänge müssen als Liste kommen."]
    cur = {p.get("ip"): p for p in ((_cache or {}).get("probes") or [])}
    out: list[dict[str, Any]] = []
    errs: list[str] = []
    seen: set[str] = set()
    for raw in value:
        if not isinstance(raw, dict):
            continue
        ip = str(raw.get("ip", "")).strip()
        if not _valid_ip(ip) or ip in seen:
            if ip and not _valid_ip(ip):
                errs.append(f"SSH-Zugang: „{ip}“ ist keine gültige IP.")
            continue
        seen.add(ip)
        pw = str(raw.get("password", ""))
        if pw == "":
            pw = (cur.get(ip) or {}).get("password", "")
        out.append({"ip": ip, "user": str(raw.get("user", "")).strip()[:64],
                    "password": pw, "enabled": bool(raw.get("enabled", True))})
    return out, errs


def _clean_pinned(value: Any) -> tuple[list[dict[str, Any]], list[str]]:
    """Angeheftete Geräte samt letztem bekannten Stand. Der Stand erlaubt es,
    ein offline gegangenes Gerät weiterhin (als offline) anzuzeigen."""
    if not isinstance(value, list):
        return [], ["Angeheftete Geräte müssen als Liste kommen."]
    out: list[dict[str, Any]] = []
    errs: list[str] = []
    seen: set[str] = set()
    for i, raw in enumerate(value, 1):
        if not isinstance(raw, dict):
            continue
        ip = str(raw.get("ip", "")).strip()
        if not _valid_ip(ip):
            errs.append(f"Angeheftet {i}: keine gültige IP.")
            continue
        if ip in seen:
            continue
        seen.add(ip)
        ports: list[int] = []
        for p in raw.get("ports", []) or []:
            try:
                pp = int(p)
            except (TypeError, ValueError):
                continue
            if 1 <= pp <= 65535 and pp not in ports:
                ports.append(pp)
        ssh = raw.get("ssh_port")
        try:
            ssh = int(ssh) if ssh not in (None, "") else None
        except (TypeError, ValueError):
            ssh = None
        txt = lambda k, n: (str(raw.get(k) or "").strip()[:n] or None)  # noqa: E731
        out.append({"ip": ip, "mac": txt("mac", 32), "hostname": txt("hostname", 120),
                    "vendor": txt("vendor", 120), "alias": txt("alias", 48),
                    "ssh_port": ssh, "ports": ports[:60]})
    return out, errs


def _clean_links(value: Any) -> tuple[list[dict[str, Any]], list[str]]:
    """Schnelllinks: Name + URL, dazu wahlweise Symbol, Farbe und Zuordnung
    zu bestimmten Dashboards (leer = überall)."""
    if not isinstance(value, list):
        return [], ["Schnelllinks müssen als Liste kommen."]
    out: list[dict[str, Any]] = []
    errs: list[str] = []
    seen_ids: set[str] = set()
    for i, raw in enumerate(value, 1):
        if not isinstance(raw, dict):
            errs.append(f"Schnelllink {i}: unerwartetes Format.")
            continue
        name = str(raw.get("name", "")).strip()[:48]
        url = _clean_url(raw.get("url", ""))
        if not name:
            errs.append(f"Schnelllink {i}: Name fehlt.")
            continue
        if not url:
            errs.append(f"„{name}“: Adresse (URL) fehlt.")
            continue
        # Symbol: ein kurzes Zeichen/Emoji, kein HTML.
        icon = re.sub(r"[<>]", "", str(raw.get("icon", "")).strip())[:8]
        color = str(raw.get("color", "")).strip()
        if color and color not in ACCENTS:
            color = ""
        boards = raw.get("boards", [])
        if isinstance(boards, str):
            boards = [b for b in re.split(r"[,\s]+", boards) if b]
        if not isinstance(boards, list):
            boards = []
        boards = [str(b).strip()[:16] for b in boards if str(b).strip()][:40]
        lid = str(raw.get("id", "")).strip()[:16] or _new_id()
        while lid in seen_ids:
            lid = _new_id()
        seen_ids.add(lid)
        out.append({"id": lid, "name": name, "url": url, "icon": icon,
                    "color": color, "boards": boards,
                    "favicon": bool(raw.get("favicon", False))})
    return out, errs


def _clean_folders(value: Any) -> tuple[list[dict[str, str]], list[str]]:
    """Selbst angelegte Ordner eines Dashboards: nur Kennung und Name."""
    if not isinstance(value, list):
        return [], ["Ordner müssen als Liste kommen."]
    out: list[dict[str, str]] = []
    errs: list[str] = []
    seen: set[str] = set()
    for i, raw in enumerate(value, 1):
        if not isinstance(raw, dict):
            errs.append(f"Ordner {i}: unerwartetes Format.")
            continue
        name = str(raw.get("name", "")).strip()[:32]
        if not name:
            continue           # namenloser Ordner = weglassen
        fid = str(raw.get("id", "")).strip()[:16] or _new_id()
        while fid in seen:
            fid = _new_id()
        seen.add(fid)
        view = str(raw.get("view", "")).strip()
        if view not in ("", "open", "collapsed"):
            view = ""
        out.append({"id": fid, "name": name, "view": view})
    return out, errs


def _clean_assign(value: Any) -> dict[str, str]:
    """Zuordnung Kachel-Schlüssel → Ordner-Kennung. Wird gegen die Ordnerliste
    erst in normalize() gerade­gezogen, damit hier nichts verlorengeht."""
    if not isinstance(value, dict):
        return {}
    out: dict[str, str] = {}
    for key, fid in value.items():
        key = str(key).strip()[:32]
        fid = str(fid).strip()[:16]
        if key and fid:
            out[key] = fid
    return out


def _clean_color(v: Any) -> str:
    """Eine Farbe akzeptieren – Hex (#abc / #aabbcc) oder leer."""
    s = str(v or "").strip()
    return s if re.fullmatch(r"#[0-9a-fA-F]{3,8}", s) else ""


def _clean_tile_styles(value: Any) -> tuple[dict[str, dict], list[str]]:
    """Eigene Gestaltung je Kachel. Bewusst tolerant: unbekannte Felder fliegen
    raus, der Rest wird sauber begrenzt, damit nichts die Seite sprengt."""
    if not isinstance(value, dict):
        return {}, ["Kachel-Designs müssen ein Objekt sein."]
    out: dict[str, dict] = {}
    for key, raw in value.items():
        if not isinstance(raw, dict):
            continue
        k = str(key).strip()[:64]
        if not k:
            continue
        bg = _clean_color(raw.get("bg"))
        grad = _clean_color(raw.get("grad"))
        image = _clean_tile_image(raw.get("image")) if raw.get("image") else ""
        icon = _clean_tile_image(raw.get("icon")) if raw.get("icon") else ""
        fg = _clean_color(raw.get("fg"))
        title = re.sub(r"[<>]", "", str(raw.get("title") or "")).strip()[:60]
        noauto = "1" if raw.get("noauto") else ""
        nostats = "1" if raw.get("nostats") else ""
        noping = "1" if raw.get("noping") else ""
        noprobe = "1" if raw.get("noprobe") else ""
        title = re.sub(r"[<>]", "", str(raw.get("title") or "")).strip()[:48]
        fit = raw.get("fit") if raw.get("fit") in ("cover", "contain", "fill") else ""
        pos = raw.get("pos") if raw.get("pos") in (
            "center", "top", "bottom", "left", "right") else ""
        # Rohes CSS nur als Deklarationen; geschweifte Klammern raus, damit
        # niemand aus einer Kachel heraus fremde Selektoren stylt.
        css = re.sub(r"[{}<>]", "", str(raw.get("css") or "")).strip()[:1000]
        entry = {kk: vv for kk, vv in
                 (("bg", bg), ("grad", grad), ("image", image), ("icon", icon),
                  ("fg", fg), ("title", title), ("noauto", noauto),
                  ("nostats", nostats), ("noprobe", noprobe),
                  ("noping", noping), ("fit", fit),
                  ("pos", pos), ("css", css)) if vv}
        if entry:
            out[k] = entry
    return out, []


def _clean_dashboards(value: Any) -> tuple[list[dict], list[str]]:
    """Dashboards sind Sichten: Name, Aufnahmeregel, Takt, Mitglieder."""
    if not isinstance(value, list):
        return [], ["Dashboards müssen als Liste kommen."]
    out: list[dict] = []
    errs: list[str] = []
    seen_ids: set[str] = set()
    for i, raw in enumerate(value, 1):
        if not isinstance(raw, dict):
            errs.append(f"Dashboard {i}: unerwartetes Format.")
            continue
        name = str(raw.get("name", "")).strip()[:32]
        if not name:
            errs.append(f"Dashboard {i}: Name fehlt.")
            continue

        try:
            refresh = int(raw.get("refresh", 0))
        except (TypeError, ValueError):
            errs.append(f"„{name}“: Prüfintervall ist keine Zahl.")
            continue
        if refresh not in REFRESH_CHOICES:
            errs.append(f"„{name}“: Prüfintervall {refresh} ist nicht erlaubt.")
            continue

        members, merr = _clean_ip_list(raw.get("members", []))
        hidden, herr = _clean_ip_list(raw.get("hidden", []))
        if merr or herr:
            errs += [f"„{name}“: {e}" for e in merr + herr]
            continue

        did = str(raw.get("id", "")).strip()[:16] or _new_id()
        while did in seen_ids:
            did = _new_id()
        seen_ids.add(did)

        layout = str(raw.get("layout", "cards")).strip()
        if layout == "tiles":      # hieß früher so
            layout = "cards"
        if layout not in LAYOUTS:
            errs.append(f"„{name}“: Ansicht „{layout}“ gibt es nicht.")
            continue

        width = str(raw.get("width", "normal")).strip()
        if width not in WIDTHS:
            errs.append(f"„{name}“: Breite „{width}“ gibt es nicht.")
            continue
        # Eigene Reihenfolge: Liste von Schlüsseln wie „192.168.178.1:80“.
        order = raw.get("order", [])
        if isinstance(order, str):
            order = [x for x in re.split(r"[,\s]+", order) if x]
        if not isinstance(order, list):
            errs.append(f"„{name}“: Reihenfolge muss eine Liste sein.")
            continue
        order = [str(x).strip()[:32] for x in order if str(x).strip()][:400]

        group = str(raw.get("group", "none")).strip()
        if group not in GROUPS:
            errs.append(f"„{name}“: Gliederung „{group}“ gibt es nicht.")
            continue
        sort = str(raw.get("sort", "natural")).strip()
        if sort not in SORTS:
            errs.append(f"„{name}“: Sortierung „{sort}“ gibt es nicht.")
            continue
        shape = str(raw.get("icon_shape", "rounded")).strip()
        if shape not in ICON_SHAPES:
            errs.append(f"„{name}“: Symbolform „{shape}“ gibt es nicht.")
            continue
        try:
            cols = int(raw.get("columns", 0))
        except (TypeError, ValueError):
            errs.append(f"„{name}“: Spaltenzahl ist keine Zahl.")
            continue
        if cols and not 2 <= cols <= 8:
            errs.append(f"„{name}“: Spalten müssen 2 bis 8 sein (0 = automatisch).")
            continue

        style = str(raw.get("tile_style", "outline")).strip()
        if style not in TILE_STYLES:
            errs.append(f"„{name}“: Kachelstil „{style}“ gibt es nicht.")
            continue
        label = str(raw.get("label_mode", "full")).strip()
        if label not in LABEL_MODES:
            errs.append(f"„{name}“: Beschriftung „{label}“ gibt es nicht.")
            continue

        folders, ferr = _clean_folders(raw.get("folders", []))
        if ferr:
            errs += [f"„{name}“: {e}" for e in ferr]
            continue
        assign = _clean_assign(raw.get("assign", {}))
        fview = str(raw.get("folder_view", "open")).strip()
        if fview not in ("open", "collapsed"):
            fview = "open"

        # Eigener Hintergrund je Dashboard – leer bedeutet „wie global“.
        dbg = raw.get("background")
        dbg = dbg if dbg in BACKGROUNDS else ""
        dgrad = raw.get("bg_grad")
        if isinstance(dgrad, dict):
            dgrad = {"a": _clean_color(dgrad.get("a")) or "#3B82F6",
                     "b": _clean_color(dgrad.get("b")) or "#8B5CF6",
                     "angle": max(0, min(360, int(dgrad.get("angle", 135) or 135)))}
        else:
            dgrad = None
        # Überschreibungen: leer/None bedeutet „gilt wie in den Einstellungen“.
        dsize = raw.get("tile_size")
        dsize = dsize if dsize in TILE_SIZES else ""
        dscheme = raw.get("scheme_filter")
        dscheme = dscheme if dscheme in SCHEME_FILTERS else ""
        dping = raw.get("show_ping")
        dping = bool(dping) if isinstance(dping, bool) else None
        dempty = raw.get("hide_empty")
        dempty = bool(dempty) if isinstance(dempty, bool) else None
        out.append({"id": did, "name": name,
                    "background": dbg, "bg_grad": dgrad,
                    "tile_size": dsize, "scheme_filter": dscheme,
                    "show_ping": dping, "hide_empty": dempty,
                    "auto_add": bool(raw.get("auto_add", True)),
                    "show_stats": bool(raw.get("show_stats", True)),
                    "show_probe": bool(raw.get("show_probe", True)),
                    "refresh": refresh, "layout": layout,
                    "tile_style": style, "label_mode": label,
                    "width": width, "order": order, "group": group,
                    "sort": sort, "icon_shape": shape, "columns": cols,
                    "only_online": bool(raw.get("only_online", False)),
                    "members": members, "hidden": hidden,
                    "seen": _clean_ip_list(raw.get("seen", []))[0],
                    "owner": str(raw.get("owner") or "")[:32],
                    "shared": [str(x)[:32] for x in (raw.get("shared") or [])
                               if isinstance(x, str)][:50],
                    "folders": folders, "assign": assign, "folder_view": fview})
    if not out and not errs:
        errs.append("Mindestens ein Dashboard muss bestehen bleiben.")
    return out, errs


def clean_hostport(raw: Any) -> tuple[str, int]:
    """Macht aus allem, was Menschen eingeben, Host und Port.

    Wer die Adresse aus dem Browser kopiert, hat „https://vcenter01.fritz.box/“
    in der Zwischenablage – nicht „vcenter01.fritz.box“. Beides muss gehen.
    """
    s = str(raw or "").strip()
    if not s:
        return "", 443
    s = s.split("://", 1)[-1]        # Schema weg
    s = s.split("/", 1)[0]           # Pfad weg
    s = s.split("?", 1)[0].strip()
    if s.startswith("[") and "]" in s:            # IPv6 in Klammern
        host, _, rest = s.partition("]")
        host = host[1:]
        port = rest.lstrip(":")
    else:
        host, _, port = s.rpartition(":")
        if not host:                  # kein Doppelpunkt -> alles ist der Host
            host, port = port, ""
    try:
        p_num = int(port) if port else 443
        if not 1 <= p_num <= 65535:
            p_num = 443
    except ValueError:
        p_num = 443
    return host.strip().strip(".")[:120], p_num


def _clean_int(value: Any, lo: int, hi: int, label: str) -> tuple[int | None, list[str]]:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None, [f"{label}: „{value}“ ist keine ganze Zahl."]
    if not lo <= n <= hi:
        return None, [f"{label} muss zwischen {lo} und {hi} liegen."]
    return n, []


def validate(patch: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Prüft ein Teil-Update. Gibt (saubere Felder, Fehlerliste) zurück."""
    clean: dict[str, Any] = {}
    errs: list[str] = []

    if "subnets" in patch:
        nets, e = _clean_subnets(patch["subnets"])
        errs += e
        if not nets and not e:
            errs.append("Mindestens ein Netz muss angegeben sein.")
        clean["subnets"] = nets

    if "ports" in patch:
        ports, e = _clean_ports(patch["ports"])
        errs += e
        clean["ports"] = ports

    if "aliases" in patch:
        al, e = _clean_alias_map(patch["aliases"])
        errs += e
        clean["aliases"] = al

    if "hidden" in patch:
        ips, e = _clean_ip_list(patch["hidden"])
        errs += e
        clean["hidden"] = ips

    if "manual_devices" in patch:
        man, e = _clean_manual(patch["manual_devices"])
        errs += e
        clean["manual_devices"] = man

    if "pinned" in patch:
        pin, e = _clean_pinned(patch["pinned"])
        errs += e
        clean["pinned"] = pin

    if "probes" in patch:
        pr, e = _clean_probes(patch["probes"])
        errs += e
        clean["probes"] = pr

    if "tile_styles" in patch:
        ts, e = _clean_tile_styles(patch["tile_styles"])
        errs += e
        clean["tile_styles"] = ts

    if "links" in patch:
        links, e = _clean_links(patch["links"])
        errs += e
        clean["links"] = links

    if "search_providers" in patch:
        prov, e = _clean_providers(patch["search_providers"])
        errs += e
        clean["search_providers"] = prov

    if "search_default" in patch:
        clean["search_default"] = re.sub(
            r"[^A-Za-z0-9]", "", str(patch["search_default"]).strip().lstrip("!"))[:8].lower()

    if "weather" in patch:
        cur = (_cache or {}).get("weather") or {}
        w, e = _clean_weather(patch["weather"], cur)
        errs += e
        clean["weather"] = w

    if "dashboards" in patch:
        dash, e = _clean_dashboards(patch["dashboards"])
        errs += e
        clean["dashboards"] = dash

    if "default_dashboard" in patch:
        clean["default_dashboard"] = str(patch["default_dashboard"]).strip()[:16]

    if "priority_ports" in patch:
        raw = patch["priority_ports"]
        if isinstance(raw, str):
            raw = [p for p in re.split(r"[,\s]+", raw) if p]
        prio: list[int] = []
        for p in raw or []:
            try:
                n = int(p)
            except (TypeError, ValueError):
                errs.append(f"Bevorzugte Ports: „{p}“ ist keine Zahl.")
                continue
            if 1 <= n <= 65535 and n not in prio:
                prio.append(n)
        clean["priority_ports"] = prio

    if "concurrency" in patch:
        n, e = _clean_int(patch["concurrency"], 1, 200, "Parallele Abfragen")
        errs += e
        if n is not None:
            clean["concurrency"] = n

    if "http_timeout" in patch:
        try:
            t = float(patch["http_timeout"])
            if not 0.5 <= t <= 30:
                errs.append("Zeitlimit muss zwischen 0,5 und 30 Sekunden liegen.")
            else:
                clean["http_timeout"] = round(t, 1)
        except (TypeError, ValueError):
            errs.append("Zeitlimit: keine gültige Zahl.")

    if "ssh_ports" in patch:
        ports, e = _clean_ports(patch["ssh_ports"])
        errs += e
        clean["ssh_ports"] = ports

    if "ssh_users" in patch:
        if not isinstance(patch["ssh_users"], dict):
            errs.append("SSH-Benutzer müssen als Zuordnung IP → Name kommen.")
        else:
            users: dict[str, str] = {}
            for ip, user in patch["ssh_users"].items():
                ip = str(ip).strip()
                # Nur harmlose Benutzernamen – niemals Passwörter.
                user = re.sub(r"[^A-Za-z0-9._@-]", "", str(user).strip())[:32]
                if not user:
                    continue
                try:
                    ipaddress.ip_address(ip)
                except ValueError:
                    errs.append(f"„{ip}“ ist keine gültige IP-Adresse.")
                    continue
                users[ip] = user
            clean["ssh_users"] = users

    if "stats_show" in patch:
        raw = patch["stats_show"]
        if isinstance(raw, str):
            raw = [x for x in re.split(r"[,\s]+", raw) if x]
        clean["stats_show"] = [x for x in ("cpu", "ram", "uptime", "load")
                               if x in (raw or [])]

    if "vsphere" in patch:
        v = patch["vsphere"]
        if not isinstance(v, dict):
            errs.append("vSphere-Zugang: unerwartetes Format.")
        else:
            cur = (_cache or {}).get("vsphere") or {}
            pw = str(v.get("password", ""))
            host, port = clean_hostport(v.get("host", ""))
            clean["vsphere"] = {
                "host": host, "port": port,
                "user": str(v.get("user", "")).strip()[:80],
                # Leer gelassen heißt: unverändert lassen, nicht löschen.
                "password": cur.get("password", "") if pw == "" else pw[:200],
                "insecure": bool(v.get("insecure", True)),
                "target": str(v.get("target", "")).strip()[:80],
                # Werte zusätzlich auf die Kacheln der Hosts legen.
                "tiles": bool(v.get("tiles", cur.get("tiles", False))),
            }

    if "proxmox" in patch:
        v = patch["proxmox"]
        if not isinstance(v, dict):
            errs.append("Proxmox-Zugang: unerwartetes Format.")
        else:
            cur = (_cache or {}).get("proxmox") or {}
            sec = str(v.get("token_secret", ""))
            clean["proxmox"] = {
                "enabled": bool(v.get("enabled", False)),
                "host": str(v.get("host", "")).strip()[:200],
                "token_id": str(v.get("token_id", "")).strip()[:120],
                # Leer = unverändert lassen, nicht löschen.
                "token_secret": cur.get("token_secret", "") if sec == "" else sec[:200],
                "insecure": bool(v.get("insecure", True)),
            }

    for key, allowed, label in (("theme", THEMES, "Erscheinungsbild"),
                                ("stats_source", STATS_SOURCES, "Systemwerte"),
                                ("stats_style", STATS_STYLES, "Anzeige der Werte"),
                                ("accent", ACCENTS, "Akzentfarbe"),
                                ("tile_size", TILE_SIZES, "Kachelgröße"),
                                ("background", BACKGROUNDS, "Hintergrund"),
                                ("scan_mode", SCAN_MODES, "Scan-Modus"),
                                ("service_naming", NAMING, "Dienstnamen"),
                                ("scheme_filter", SCHEME_FILTERS, "Schema-Filter"),
                                ("font", FONTS, "Schriftart"),
                                ("net_mode", NET_MODES, "Betriebsart")):
        if key in patch:
            v = str(patch[key]).strip()
            if v not in allowed:
                errs.append(f"{label}: „{v}“ gibt es nicht.")
            else:
                clean[key] = v

    if "_fontv" in patch:
        try:
            clean["_fontv"] = max(0, int(patch["_fontv"]))
        except (TypeError, ValueError):
            pass
    if "update_seen" in patch:
        clean["update_seen"] = str(patch["update_seen"] or "")[:32]
    for key in ("favicons", "auto_icons", "resolve_names", "show_ping", "auto_tint",
                "update_check", "mono_custom",
                "configured", "hide_empty", "ssh_enabled"):
        if key in patch:
            clean[key] = bool(patch[key])

    if "bg_grad" in patch:
        g = patch["bg_grad"] if isinstance(patch["bg_grad"], dict) else {}
        a = _clean_color(g.get("a")) or "#3B82F6"
        b = _clean_color(g.get("b")) or "#8B5CF6"
        try:
            ang = int(g.get("angle", 135)) % 360
        except (TypeError, ValueError):
            ang = 135
        clean["bg_grad"] = {"a": a, "b": b, "angle": ang}

    return clean, errs


# --- Zusammenhänge geradeziehen ---------------------------------------------
def normalize(cfg: dict[str, Any]) -> dict[str, Any]:
    """Hält Verweise zwischen den Feldern heil. Läuft bei jedem Speichern.

    So kann keine Bedienung einen Zustand hinterlassen, aus dem man
    nicht mehr herauskommt – etwa eine Startansicht, die es nicht gibt.
    """
    dash = cfg.get("dashboards") or []
    if cfg.get("configured") and not dash:
        dash = [{"id": _new_id(), "name": "Übersicht", "auto_add": True,
                 "refresh": 0, "layout": "cards", "tile_style": "outline",
                 "label_mode": "full", "width": "normal", "order": [],
                 "group": "none", "sort": "natural", "icon_shape": "rounded",
                 "columns": 0, "only_online": False, "members": [], "hidden": []}]
        cfg["dashboards"] = dash

    # Ältere Dashboards kennen die neuen Felder noch nicht.
    for d in dash:
        d.setdefault("layout", "cards")
        # „tiles“ war die Vorgängerform der Karten.
        if d.get("layout") == "tiles":
            d["layout"] = "cards"
        d.setdefault("tile_style", "outline")
        d.setdefault("label_mode", "full")
        d.setdefault("width", "normal")
        d.setdefault("order", [])
        d.setdefault("group", "none")
        d.setdefault("sort", "natural")
        d.setdefault("icon_shape", "rounded")
        d.setdefault("columns", 0)
        d.setdefault("only_online", False)
        # Früher gab es hier einen zweiten SSH-Schalter. Ein Schalter an zwei
        # Orten heißt: man dreht am einen und nichts passiert. Jetzt entscheidet
        # allein die globale Einstellung.
        d.pop("show_ssh", None)
        # Selbst angelegte Ordner samt Zuordnung.
        d.setdefault("folders", [])
        d.setdefault("assign", {})
        d.setdefault("folder_view", "open")
        # Zuordnungen auf existierende Ordner beschränken – sonst zeigt eine
        # Kachel auf einen gelöschten Ordner und verschwindet still.
        fids = {f["id"] for f in d.get("folders", []) if isinstance(f, dict)}
        d["assign"] = {k: v for k, v in (d.get("assign") or {}).items()
                       if v in fids}

    ids = {d["id"] for d in dash}

    # Schnelllinks: Verweise auf gelöschte Dashboards fallen weg (leer = überall).
    links = cfg.get("links")
    if isinstance(links, list):
        for lk in links:
            if isinstance(lk, dict):
                lk["boards"] = [b for b in lk.get("boards", []) if b in ids]
    if dash and cfg.get("default_dashboard") not in ids:
        cfg["default_dashboard"] = dash[0]["id"]
    if not dash:
        cfg["default_dashboard"] = ""

    # Der vSphere-Zugang wird ebenfalls geradegezogen: Wer früher eine ganze
    # URL eingetragen hat, soll sie nicht ewig mit sich herumtragen.
    vs = cfg.get("vsphere")
    if isinstance(vs, dict):
        host, port = clean_hostport(vs.get("host"))
        vs["host"] = host
        if not vs.get("port"):
            vs["port"] = port
        vs.setdefault("user", "")
        vs.setdefault("password", "")
        vs.setdefault("insecure", True)
        vs.setdefault("target", "")
    else:
        cfg["vsphere"] = dict(DEFAULTS["vsphere"])

    # Überall deaktivierte Geräte müssen nirgends mehr als Mitglied hängen.
    gone = set(cfg.get("hidden") or [])
    for d in dash:
        d["members"] = [ip for ip in d.get("members", []) if ip not in gone]
        d["hidden"] = [ip for ip in d.get("hidden", []) if ip not in gone]
    return cfg


def _migrate(cfg: dict[str, Any]) -> dict[str, Any]:
    """Ältere Stände übernehmen, ohne dass jemand neu einrichten muss."""
    # Früher gab es einen globalen Dauerlauf. Der lebt jetzt am Dashboard.
    old_auto = cfg.pop("auto_scan", None)
    old_iv = cfg.pop("scan_interval", None)
    if cfg.get("configured") and not cfg.get("dashboards"):
        refresh = 0
        if old_auto and isinstance(old_iv, int):
            refresh = min(REFRESH_CHOICES, key=lambda c: abs(c - old_iv) if c else 1e9)
        cfg["dashboards"] = [{"id": _new_id(), "name": "Übersicht",
                              "auto_add": True, "refresh": refresh,
                              "layout": "cards", "tile_style": "outline",
                              "label_mode": "full", "width": "normal",
                              "order": [], "group": "none", "sort": "natural",
                              "icon_shape": "rounded", "columns": 0,
                              "only_online": False, "members": [], "hidden": []}]

    # Portliste: neue Standard-Web-Ports einmalig in bestehende Configs übernehmen.
    if int(cfg.get("_ports_mig", 0) or 0) < PORTS_MIG:
        have = [p.strip() for p in str(cfg.get("ports") or "").split(",") if p.strip()]
        seen = set(have)
        for p in PORTS_MIG_ADD.split(","):
            p = p.strip()
            if p and p not in seen:
                have.append(p); seen.add(p)
        cfg["ports"] = ",".join(have)
        cfg["_ports_mig"] = PORTS_MIG
    return cfg


# --- Persistenz --------------------------------------------------------------
def _seed_from_env() -> dict[str, Any]:
    """Nur beim allerersten Start: ENV als Vorbelegung übernehmen."""
    seed: dict[str, Any] = {}
    if os.getenv("NETBOARD_SUBNETS"):
        seed["subnets"] = os.environ["NETBOARD_SUBNETS"]
    if os.getenv("NETBOARD_PORTS"):
        seed["ports"] = os.environ["NETBOARD_PORTS"]
    if os.getenv("NETBOARD_HTTP_TIMEOUT"):
        seed["http_timeout"] = os.environ["NETBOARD_HTTP_TIMEOUT"]
    if os.getenv("NETBOARD_CONCURRENCY"):
        seed["concurrency"] = os.environ["NETBOARD_CONCURRENCY"]
    clean, _ = validate(seed)
    if clean.get("subnets"):   # vorgegebene Netze = Einrichtung erledigt
        clean["configured"] = True
    return clean


# Felder, die verschlüsselt auf die Platte gehen (im Speicher bleiben sie
# Klartext, weil der Server sie zum Verbinden braucht).
_SECRET_PATHS = (("vsphere", "password"), ("proxmox", "token_secret"))


def _write(cfg: dict[str, Any]) -> None:
    """Schreibt atomar. Erwartet, dass _lock bereits gehalten wird.
    Dienst-Geheimnisse werden dabei verschlüsselt – auf der Platte nie Klartext."""
    from . import secretstore as ss
    disk = dict(cfg)
    for sect, key in _SECRET_PATHS:
        d = disk.get(sect)
        if isinstance(d, dict) and d.get(key):
            d = dict(d)
            d[key] = ss.encrypt(d[key])
            disk[sect] = d
    if isinstance(disk.get("probes"), list):
        disk["probes"] = [{**p, "password": ss.encrypt(p.get("password", ""))}
                          if isinstance(p, dict) else p for p in disk["probes"]]
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = CONF_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(disk, indent=2, ensure_ascii=False), "utf-8")
        tmp.replace(CONF_FILE)  # atomar: nie halb geschriebene Datei
    except OSError as exc:
        raise RuntimeError(f"Einstellungen nicht speicherbar: {exc}") from exc


def load() -> dict[str, Any]:
    global _cache
    with _lock:
        if _cache is not None:
            return dict(_cache)
        cfg = dict(DEFAULTS)
        on_disk: dict[str, Any] = {}
        if CONF_FILE.exists():
            try:
                stored = json.loads(CONF_FILE.read_text("utf-8"))
                if isinstance(stored, dict):
                    on_disk = stored
                    cfg.update({k: v for k, v in stored.items()
                                if k in DEFAULTS or k in ("auto_scan", "scan_interval")})
            except (json.JSONDecodeError, OSError):
                pass  # kaputte Datei -> Defaults, Nutzer richtet neu ein
        else:
            cfg.update(_seed_from_env())
        cfg = normalize(_migrate(cfg))

        # Verschlüsselte Dienst-Geheimnisse wieder in Klartext bringen – der
        # Server braucht sie zum Verbinden. An den Browser gehen sie nie.
        from . import secretstore as ss
        for sect, key in _SECRET_PATHS:
            d = cfg.get(sect)
            if isinstance(d, dict) and ss.is_encrypted(d.get(key, "")):
                d = dict(d)
                d[key] = ss.decrypt(d[key])
                cfg[sect] = d
        if isinstance(cfg.get("probes"), list):
            cfg["probes"] = [{**p, "password": ss.decrypt(p.get("password", ""))}
                             if isinstance(p, dict) else p for p in cfg["probes"]]

        # Hat die Migration etwas verändert, muss das auf Platte – sonst bekäme
        # ein migriertes Dashboard bei jedem Neustart eine neue Kennung.
        if cfg != {**DEFAULTS, **on_disk} and CONF_FILE.exists():
            try:
                _write(cfg)
            except RuntimeError:
                pass  # nur lesbar? Dann läuft es eben im Speicher weiter.
        _cache = cfg
        return dict(cfg)


def save(patch: dict[str, Any]) -> dict[str, Any]:
    """Übernimmt bereits validierte Felder und schreibt atomar auf Platte."""
    global _cache
    with _lock:
        cfg = dict(_cache) if _cache is not None else dict(DEFAULTS)
        cfg.update(patch)
        cfg = normalize(cfg)
        _write(cfg)
        _cache = cfg
        return dict(cfg)
