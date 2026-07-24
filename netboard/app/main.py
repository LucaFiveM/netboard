"""Netboard – Auto-Discovery-Launchpad fürs Heimnetz."""
from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import io
import re
import time

import httpx
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path

from fastapi import Body, FastAPI, Request, Response, WebSocket
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import config as C
from . import sshgw
from . import sysinfo
from .enrich import ICONS, enrich
from .scanner import detect_gateway, detect_subnets, discover, sort_services

STATIC = Path(__file__).parent / "static"

STATE: dict = {
    "devices": [], "found": [], "hidden": [], "meta": {}, "scanning": False,
    "last_scan": None, "error": None, "stats": None, "tile_stats": {},
    "probes_result": {}, "updates": {},
}
_scan_lock = asyncio.Lock()
# Weckt die Werte-Schleife, wenn jemand an den Einstellungen dreht. Ohne das
# stünde nach dem Umschalten bis zu 30 Sekunden der alte Wert da.
_stats_wake = asyncio.Event()


def _serialize(devices) -> list[dict]:
    # asdict() kennt keine @property -> label explizit ergänzen
    return [{**asdict(d), "label": d.label} for d in devices]


def _snapshot(d: dict) -> dict:
    """Letzter bekannter Stand eines Geräts – genug, um es offline zu zeigen."""
    return {"ip": d["ip"], "mac": d.get("mac"), "hostname": d.get("hostname"),
            "vendor": d.get("vendor"), "alias": d.get("alias"),
            "ssh_port": d.get("ssh_port"),
            "ports": sorted(s["port"] for s in d.get("services", []))}


def _offline_from_snapshot(snap: dict) -> dict:
    """Gerätedatensatz allein aus dem Snapshot – alle Dienste als offline."""
    ip = snap["ip"]
    label = (snap.get("alias") or snap.get("hostname")
             or (f"{snap['vendor']} .{ip.rsplit('.', 1)[-1]}" if snap.get("vendor") else ip))
    services = [{"port": p, "scheme": "https" if p in (443, 8443) else "http",
                 "path": "", "title": None, "ok": False, "icon": False}
                for p in snap.get("ports", [])]
    return {"ip": ip, "hostname": snap.get("hostname"), "mac": snap.get("mac"),
            "vendor": snap.get("vendor"), "is_gateway": False, "services": services,
            "alias": snap.get("alias"), "manual": False, "manual_id": None,
            "ssh_port": snap.get("ssh_port"), "label": label,
            "pinned": True, "offline": True}


def _apply_pins(found: list[dict], cfg: dict) -> tuple[list[dict], list[dict]]:
    """Angeheftete Geräte einweben und je nach Modus die Anzeige bestimmen.

    Rückgabe (Anzeige, alle Gefundenen). Snapshots angehefteter Geräte werden
    dabei aufgefrischt – aber nur bei echter Änderung auf Platte geschrieben.
    """
    pins = {p["ip"]: p for p in (cfg.get("pinned") or []) if p.get("ip")}
    found_ips = {d["ip"] for d in found}

    changed = False
    for d in found:
        if d["ip"] in pins:
            d["pinned"] = True
            fresh = _snapshot(d)
            if fresh != pins[d["ip"]]:
                pins[d["ip"]] = fresh
                changed = True
    if changed:
        C.save({"pinned": list(pins.values())})

    offline = [_offline_from_snapshot(pins[ip]) for ip in pins if ip not in found_ips]

    if cfg.get("scan_mode", "auto") == "pinned":
        shown = [d for d in found if d["ip"] in pins or d.get("manual")] + offline
    else:
        shown = found + offline
    return shown, found


async def run_scan() -> None:
    """Ein Suchlauf. Parallele Aufrufe prallen ab, statt sich zu stapeln."""
    if _scan_lock.locked():
        return
    async with _scan_lock:
        STATE["scanning"] = True
        try:
            cfg = C.load()
            devices, hidden, meta = await discover(cfg)
            await enrich(devices, cfg)
            # Erreichbarkeit steht erst nach dem Anreichern fest -> neu sortieren
            prio = cfg.get("priority_ports") or []
            for d in devices:
                d.services = sort_services(d.services, prio)
            STATE["found"] = _serialize(devices)
            STATE["devices"], _ = _apply_pins(
                [dict(d) for d in STATE["found"]], cfg)
            STATE["hidden"] = _serialize(hidden)
            STATE["meta"] = meta
            STATE["last_scan"] = time.time()
            STATE["error"] = None
            # Erreichbarkeit für den 24-Stunden-Verlauf festhalten. Ein Gerät
            # gilt als erreichbar, wenn wenigstens ein Dienst geantwortet hat.
            try:
                from . import uptime
                uptime.record({d["ip"]: any(s.get("ok") for s in (d.get("services") or []))
                               for d in STATE["found"]})
            except Exception:
                pass        # Der Verlauf ist Beiwerk – er darf nie den Scan kippen
        except Exception as exc:  # ein Scanfehler darf den Dienst nie beenden
            STATE["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            STATE["scanning"] = False


async def _startup_scan() -> None:
    """Einmal beim Start. Danach gibt das geöffnete Dashboard den Takt vor –
    schaut niemand hin, wird auch nicht gescannt."""
    cfg = C.load()
    if cfg.get("configured") and cfg.get("subnets"):
        await run_scan()


async def _stats_loop() -> None:
    """Systemwerte im Hintergrund. Der lokale Weg kostet nichts; vSphere ist
    teuer und wird deshalb deutlich seltener gefragt."""
    loop = asyncio.get_running_loop()
    while True:
        cfg = C.load()
        src = cfg.get("stats_source", "local")
        if src == "off":
            STATE["stats"] = None
            try:
                await asyncio.wait_for(_stats_wake.wait(), timeout=5)
            except asyncio.TimeoutError:
                pass
            _stats_wake.clear()
            continue
        try:
            # Beide Quellen blockieren (Dateien bzw. SOAP) -> in den Thread.
            STATE["stats"] = await loop.run_in_executor(None, sysinfo.read, cfg)
        except Exception as exc:
            STATE["stats"] = {"source": src, "error": f"{type(exc).__name__}: {exc}"}
        try:
            await asyncio.wait_for(_stats_wake.wait(),
                                   timeout=30 if src == "vsphere" else 3)
        except asyncio.TimeoutError:
            pass
        _stats_wake.clear()


async def _tile_loop() -> None:
    """Live-Werte der Integrationen (Proxmox-Nodes, ESXi-Hosts) je IP. Läuft
    ruhig im Hintergrund und legt sich schlafen, wenn nichts eingerichtet ist."""
    from . import integrations
    while True:
        cfg = C.load()
        vs = cfg.get("vsphere") or {}
        active = (cfg.get("proxmox") or {}).get("enabled") \
            or (vs.get("host") and (cfg.get("stats_source") == "vsphere" or vs.get("tiles")))
        if not active:
            STATE["tile_stats"] = {}
            await asyncio.sleep(10)
            continue
        try:
            STATE["tile_stats"] = await integrations.read_all(cfg)
        except Exception:
            pass   # eine kaputte Integration darf das Board nie stören
        await asyncio.sleep(20)


@asynccontextmanager
async def lifespan(app: FastAPI):
    tasks = [asyncio.create_task(_startup_scan()),
             asyncio.create_task(_stats_loop()),
             asyncio.create_task(_tile_loop()),
             asyncio.create_task(_probe_loop())]
    yield
    for task in tasks:
        task.cancel()


app = FastAPI(title="Netboard", lifespan=lifespan)

# --- Optionaler Login --------------------------------------------------------
_AUTH_OPEN = ("/login", "/api/auth/login", "/api/auth/status",
              "/favicon.ico", "/custom.css", "/font.css", "/font.file")


@app.middleware("http")
async def _auth_gate(request: Request, call_next):
    """Ist der Login aktiv, kommt ohne gültige Sitzung nur an Login-Seite und
    -API sowie an statische Dateien heran. Alles andere wird abgewiesen."""
    from . import secretstore as ss
    auth = C.load().get("auth") or {}
    if not auth.get("enabled"):
        return await call_next(request)
    path = request.url.path
    if path in _AUTH_OPEN or path.startswith("/static/"):
        return await call_next(request)
    if ss.check_session(request.cookies.get("nb_session", "")):
        return await call_next(request)
    if path.startswith("/api/") or path.startswith("/ws/"):
        return JSONResponse({"error": "auth", "login": True}, status_code=401)
    return RedirectResponse("/login", status_code=302)


@app.get("/api/auth/status")
async def api_auth_status(request: Request):
    from . import secretstore as ss
    auth = C.load().get("auth") or {}
    return {"enabled": bool(auth.get("enabled")),
            "authed": bool(ss.check_session(request.cookies.get("nb_session", ""))),
            "username": auth.get("username", "admin")}


def _visible_dashboards(request: Request, cfg: dict) -> list[dict]:
    from . import users as UU
    return UU.visible_dashboards(cfg.get("dashboards") or [], current_user(request))


def current_user(request: Request) -> dict | None:
    """Der angemeldete Benutzer – oder None, wenn ohne Login gearbeitet wird."""
    from . import secretstore as ss
    from . import users as UU
    name = ss.check_session(request.cookies.get("nb_session", ""))
    return UU.by_name(name) if name else None


@app.get("/api/me")
async def api_me(request: Request):
    """Wer bin ich, und was darf ich?"""
    from . import users as UU
    u = current_user(request)
    auth = C.load().get("auth") or {}
    return {"login_enabled": bool(auth.get("enabled")),
            "user": UU.public(u) if u else None,
            "is_admin": bool(u and u.get("role") == "admin") or not auth.get("enabled"),
            "multiuser": len(UU.all_users()) > 1}


@app.post("/api/auth/login")
async def api_auth_login(body: dict = Body(...)):
    from . import secretstore as ss
    from . import users as UU
    auth = C.load().get("auth") or {}
    if not auth.get("enabled"):
        return {"ok": True}
    UU.migrate_legacy()
    name = str(body.get("username", "")).strip()
    pw = str(body.get("password", ""))
    u = UU.check_login(name, pw)
    if not u:
        # Rückfallebene: ganz altes Einzel-Login ohne Benutzerliste
        if name == auth.get("username") and ss.verify_password(pw, auth.get("password_hash", "")):
            resp = JSONResponse({"ok": True})
            resp.set_cookie("nb_session", ss.make_session(name), httponly=True,
                            samesite="lax", max_age=30 * 86400, path="/")
            return resp
        return JSONResponse({"ok": False, "error": "Benutzer oder Passwort stimmt nicht."},
                            status_code=401)
    if u.get("totp_secret"):
        given = str(body.get("code", "")).strip()
        if not given:
            # Kein Fehler, sondern die Aufforderung zum zweiten Schritt.
            return JSONResponse({"ok": False, "need_2fa": True}, status_code=401)
        if not UU.check_2fa(u["id"], given):
            return JSONResponse({"ok": False, "need_2fa": True,
                                 "error": "Der Code stimmt nicht."}, status_code=401)
    resp = JSONResponse({"ok": True, "user": UU.public(u)})
    resp.set_cookie("nb_session", ss.make_session(u["name"]), httponly=True,
                    samesite="lax", max_age=30 * 86400, path="/")
    return resp


# --- Benutzerkonten -----------------------------------------------------------
def _need_admin(request: Request):
    """Gibt eine Fehlerantwort zurück, wenn der Aufrufer kein Verwalter ist."""
    from . import users as UU
    auth = C.load().get("auth") or {}
    if not auth.get("enabled"):
        return None                       # ohne Login gibt es keine Rollen
    u = current_user(request)
    if u and u.get("role") == "admin":
        return None
    return JSONResponse({"ok": False, "errors": ["Nur für Verwalter."]}, status_code=403)


@app.get("/api/users")
async def api_users_list(request: Request):
    from . import users as UU
    deny = _need_admin(request)
    if deny:
        # Normale Benutzer bekommen nur die Namen – fürs Teilen reicht das.
        me = current_user(request)
        return {"users": [{"id": u["id"], "name": u["name"]} for u in UU.all_users()],
                "limited": True, "me": (me or {}).get("id", "")}
    me = current_user(request)
    return {"users": [UU.public(u) for u in UU.all_users()],
            "limited": False, "me": (me or {}).get("id", "")}


@app.post("/api/users")
async def api_users_add(request: Request, body: dict = Body(...)):
    from . import users as UU
    deny = _need_admin(request)
    if deny:
        return deny
    user, errs = UU.create(str(body.get("name", "")), str(body.get("password", "")),
                           str(body.get("role", "user")))
    if errs:
        return JSONResponse({"ok": False, "errors": errs}, status_code=400)
    return {"ok": True, "user": UU.public(user)}


@app.put("/api/users/{uid}")
async def api_users_update(uid: str, request: Request, body: dict = Body(...)):
    from . import users as UU
    me = current_user(request)
    auth = C.load().get("auth") or {}
    is_admin = (not auth.get("enabled")) or (me and me.get("role") == "admin")
    if not is_admin and (not me or me["id"] != uid):
        return JSONResponse({"ok": False, "errors": ["Nur für Verwalter."]}, status_code=403)
    user, errs = UU.update(uid,
                           name=body.get("name"),
                           password=body.get("password"),
                           role=body.get("role") if is_admin else None)
    if errs:
        return JSONResponse({"ok": False, "errors": errs}, status_code=400)
    return {"ok": True, "user": UU.public(user)}


@app.delete("/api/users/{uid}")
async def api_users_del(uid: str, request: Request):
    from . import users as UU
    deny = _need_admin(request)
    if deny:
        return deny
    okd, errs = UU.delete(uid)
    if not okd:
        return JSONResponse({"ok": False, "errors": errs}, status_code=400)
    return {"ok": True}


# --- Zwei-Faktor --------------------------------------------------------------
@app.post("/api/2fa/start")
async def api_2fa_start(request: Request):
    from . import users as UU
    me = current_user(request)
    if not me:
        return JSONResponse({"ok": False, "errors": ["Nicht angemeldet."]}, status_code=403)
    data, errs = UU.start_2fa(me["id"])
    if errs:
        return JSONResponse({"ok": False, "errors": errs}, status_code=400)
    # Serverzeit mitgeben: Einmalcodes hängen an der Uhr. Weicht die des
    # Servers ab, schlägt jeder Code fehl – das soll man sofort sehen.
    return {"ok": True, "server_time": int(time.time()), **data}


@app.post("/api/2fa/enable")
async def api_2fa_enable(request: Request, body: dict = Body(...)):
    from . import users as UU
    me = current_user(request)
    if not me:
        return JSONResponse({"ok": False, "errors": ["Nicht angemeldet."]}, status_code=403)
    codes, errs = UU.enable_2fa(me["id"], str(body.get("secret", "")),
                                str(body.get("code", "")))
    if errs:
        return JSONResponse({"ok": False, "errors": errs}, status_code=400)
    return {"ok": True, "recovery": codes}


@app.post("/api/2fa/disable")
async def api_2fa_disable(request: Request, body: dict = Body(default={})):
    from . import users as UU
    me = current_user(request)
    if not me:
        return JSONResponse({"ok": False, "errors": ["Nicht angemeldet."]}, status_code=403)
    # Zum Abschalten noch einmal einen gültigen Code verlangen.
    if me.get("totp_secret") and not UU.check_2fa(me["id"], str(body.get("code", ""))):
        return JSONResponse({"ok": False, "errors": ["Bitte einen gültigen Code eingeben."]},
                            status_code=400)
    UU.disable_2fa(me["id"])
    return {"ok": True}


@app.post("/api/auth/logout")
async def api_auth_logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("nb_session", path="/")
    return resp


@app.post("/api/auth/config")
async def api_auth_config(body: dict = Body(...)):
    """Login ein-/ausschalten oder Passwort ändern. Sobald der Login aktiv ist,
    schützt die Middleware diese Route – Änderungen brauchen dann eine Sitzung."""
    from . import secretstore as ss
    cur = C.load().get("auth") or {}
    username = (str(body.get("username", "")).strip() or cur.get("username") or "admin")[:64]
    newpw = str(body.get("password", ""))
    ph = cur.get("password_hash", "")
    if newpw:
        if len(newpw) < 4:
            return JSONResponse({"ok": False, "errors": ["Passwort ist zu kurz (min. 4 Zeichen)."]},
                                status_code=400)
        ph = ss.hash_password(newpw)
    enabled = bool(body.get("enabled"))
    if enabled and not ph:
        return JSONResponse({"ok": False, "errors": ["Zum Aktivieren bitte ein Passwort setzen."]},
                            status_code=400)
    C.save({"auth": {"enabled": enabled and bool(ph), "username": username, "password_hash": ph}})
    resp = JSONResponse({"ok": True, "enabled": enabled and bool(ph)})
    if enabled and ph:
        # Wer gerade aktiviert, bleibt angemeldet – sonst sperrt man sich aus.
        resp.set_cookie("nb_session", ss.make_session(username), httponly=True,
                        samesite="lax", max_age=30 * 86400, path="/")
    return resp


@app.get("/login")
async def login_page():
    return FileResponse(STATIC / "login.html", headers={"Cache-Control": "no-cache"})


def _apply(patch: dict, rescan: bool = False):
    """Validieren, speichern, optional neu suchen. Gibt (antwort, code) zurück."""
    clean, errs = C.validate(patch)
    if errs:
        return {"ok": False, "errors": errs}, 400
    try:
        cfg = C.save(clean)
    except RuntimeError as exc:
        return {"ok": False, "errors": [str(exc)]}, 500
    if rescan:
        asyncio.create_task(run_scan())
    if {"stats_source", "stats_style", "stats_show", "vsphere"} & set(clean):
        _stats_wake.set()      # sofort neu messen statt den Takt abzuwarten
    return {"ok": True, "config": cfg}, 200


# --- Zustand -----------------------------------------------------------------
@app.get("/api/state")
async def api_state(request: Request):
    """Alle Scandaten in einem Rutsch. Dashboards filtern im Browser –
    ein Wechsel kostet damit keinen Request."""
    cfg = C.load()
    gone = set(cfg.get("hidden") or [])
    devices = [d for d in STATE["devices"] if d["ip"] not in gone]
    if cfg.get("hide_empty"):
        # Eigene Geräte bleiben sichtbar – die stehen ja bewusst dort.
        devices = [d for d in devices if d["services"] or d.get("manual")]

    seen = {d["ip"]: d for d in STATE["hidden"]}
    hidden = [seen.get(ip, {"ip": ip, "label": ip, "hostname": None,
                            "vendor": None, "services": []})
              for ip in (cfg.get("hidden") or [])]

    return JSONResponse({
        "devices": devices, "hidden": hidden, "meta": STATE["meta"],
        "scanning": STATE["scanning"], "last_scan": STATE["last_scan"],
        "error": STATE["error"], "count": len(devices),
        "online": sum(1 for d in devices if any(s["ok"] for s in d["services"])),
        "configured": bool(cfg.get("configured")),
        # Jeder sieht nur seine eigenen und die mit ihm geteilten Dashboards.
        "dashboards": _visible_dashboards(request, cfg),
        "links": cfg.get("links") or [],
        "default_dashboard": cfg.get("default_dashboard") or "",
        "ssh_enabled": bool(cfg.get("ssh_enabled")),
        "stats": STATE["stats"],
        "stats_style": cfg.get("stats_style", "bars"),
        "stats_show": cfg.get("stats_show") or [],
        "background": cfg.get("background", "plain"),
        "scan_mode": cfg.get("scan_mode", "auto"),
        "pinned": [p["ip"] for p in (cfg.get("pinned") or [])],
        "tile_styles": cfg.get("tile_styles") or {},
        "tile_stats": STATE.get("tile_stats") or {},
        "probes_result": STATE.get("probes_result") or {},
        "probes": [{"ip": p["ip"], "user": p.get("user", ""),
                    "enabled": bool(p.get("enabled", True))}
                   for p in (cfg.get("probes") or [])],
        "found_count": len(STATE.get("found") or []),
        "search_providers": cfg.get("search_providers") or [],
        "search_default": cfg.get("search_default", ""),
        "weather": cfg.get("weather") or {},
    })


@app.post("/api/scan")
async def api_scan():
    asyncio.create_task(run_scan())
    return {"started": True}


# --- Einstellungen -----------------------------------------------------------
@app.get("/api/config")
async def api_get_config(request: Request):
    cfg = C.load()
    # Das vSphere-Passwort verlässt den Server nicht.
    vs = dict(cfg.get("vsphere") or {})
    if vs.get("password"):
        vs["password"] = ""
        vs["has_password"] = True
    # Ebenso das Proxmox-Token-Secret.
    px = dict(cfg.get("proxmox") or {})
    if px.get("token_secret"):
        px["token_secret"] = ""
        px["has_secret"] = True
    # Login-Hash verlässt den Server nie.
    au = dict(cfg.get("auth") or {})
    au["has_password"] = bool(au.get("password_hash"))
    au.pop("password_hash", None)
    probes = [{**p, "password": "", "has_password": bool(p.get("password"))}
              for p in (cfg.get("probes") or [])]
    # Benutzerkonten (Hashes, 2FA-Geheimnisse) gehören nicht in die Konfiguration.
    users = [{"id": u["id"], "name": u["name"], "role": u.get("role", "user"),
              "has_2fa": bool(u.get("totp_secret"))} for u in (cfg.get("users") or [])]
    cfg = {**cfg, "vsphere": vs, "proxmox": px, "auth": au, "probes": probes,
           "users": users, "dashboards": _visible_dashboards(request, cfg)}
    return cfg


@app.put("/api/config")
async def api_put_config(patch: dict = Body(...)):
    # Nur was das Ergebnis verändert, stößt einen neuen Lauf an.
    touches_scan = {"subnets", "ports", "aliases", "priority_ports",
                    "favicons", "manual_devices", "ssh_enabled",
                    "ssh_ports"} & set(patch)
    result, code = _apply(patch, rescan=bool(touches_scan))
    if code == 200 and ({"scan_mode", "pinned"} & set(patch)):
        _refresh_shown()   # Anzeige sofort neu ableiten, ohne auf einen Scan zu warten
    return JSONResponse(result, status_code=code)


@app.post("/api/stats/test")
async def api_stats_test(body: dict = Body(...)):
    """Zugang ausprobieren, ohne ihn zu speichern."""
    conf = dict((C.load().get("vsphere") or {}))
    conf.update({k: v for k, v in body.items()
                 if k in ("host", "user", "password", "insecure", "target")})
    if not (body.get("password") or "").strip():
        conf["password"] = (C.load().get("vsphere") or {}).get("password", "")
    loop = asyncio.get_running_loop()
    # Beim Testen gleich die wählbaren Cluster und Hosts mitbringen –
    # dann muss niemand Namen abtippen.
    res = await loop.run_in_executor(None, sysinfo.read_vsphere, conf, True)
    return res


@app.get("/api/detect")
async def api_detect():
    """Was der Assistent vorschlägt: erkannte Netze des Hosts."""
    subnets = await detect_subnets()
    gateway = await detect_gateway()
    return {"subnets": subnets, "gateway": gateway, "host_mode": bool(subnets)}


@app.post("/api/setup")
async def api_setup(body: dict = Body(...)):
    patch = {k: v for k, v in body.items() if k in C.DEFAULTS}
    patch["configured"] = True
    clean, errs = C.validate(patch)
    if errs:
        return JSONResponse({"ok": False, "errors": errs}, status_code=400)
    if not clean.get("subnets"):
        return JSONResponse(
            {"ok": False, "errors": ["Mindestens ein Netz muss angegeben sein."]},
            status_code=400)
    cfg = C.save(clean)   # normalize() legt das erste Dashboard an, falls keines kam
    asyncio.create_task(run_scan())
    return {"ok": True, "config": cfg}


@app.post("/api/reset")
async def api_reset():
    """Einrichtung erneut durchlaufen. Dashboards bleiben erhalten."""
    return {"ok": True, "config": C.save({"configured": False})}


# --- Anheften: Geräte festsetzen ---------------------------------------------
def _refresh_shown() -> None:
    """Anzeige neu aus den zuletzt gefundenen Geräten ableiten – ohne Rescan,
    damit Anheften sofort wirkt."""
    cfg = C.load()
    STATE["devices"], _ = _apply_pins([dict(d) for d in (STATE.get("found") or [])], cfg)


@app.get("/api/found")
async def api_found():
    """Alle beim letzten Lauf gefundenen Geräte – Grundlage für die Auswahl,
    welche fest aufgenommen werden."""
    pins = {p["ip"] for p in (C.load().get("pinned") or [])}
    out = []
    for d in (STATE.get("found") or []):
        out.append({
            "ip": d["ip"], "label": d.get("label") or d["ip"],
            "hostname": d.get("hostname"), "vendor": d.get("vendor"),
            "mac": d.get("mac"), "manual": bool(d.get("manual")),
            "services": len(d.get("services") or []),
            "online": any(s.get("ok") for s in (d.get("services") or [])),
            "pinned": d["ip"] in pins,
        })
    return {"found": out, "scanning": STATE["scanning"], "scan_mode": C.load().get("scan_mode", "auto")}


@app.post("/api/pin")
async def api_pin(body: dict = Body(...)):
    """Ausgewählte gefundene Geräte anheften (Snapshot aus dem letzten Lauf)."""
    ips = body.get("ips") or []
    if isinstance(ips, str):
        ips = [ips]
    by_ip = {d["ip"]: d for d in (STATE.get("found") or [])}
    cfg = C.load()
    pins = {p["ip"]: p for p in (cfg.get("pinned") or [])}
    for ip in ips:
        d = by_ip.get(ip)
        if d:
            pins[ip] = _snapshot(d)
    cfg = C.save({"pinned": list(pins.values())})
    _refresh_shown()
    return {"ok": True, "config": cfg}


@app.post("/api/pin/all")
async def api_pin_all(body: dict = Body(default={})):
    """Alles gerade Gefundene festsetzen – „so wie es jetzt ist“. Optional
    zugleich in den Modus wechseln, in dem nur Angeheftetes erscheint."""
    found = STATE.get("found") or []
    pins = {d["ip"]: _snapshot(d) for d in found if not d.get("manual")}
    patch = {"pinned": list(pins.values())}
    if body.get("mode") in ("auto", "pinned"):
        patch["scan_mode"] = body["mode"]
    cfg = C.save(C.validate(patch)[0])
    _refresh_shown()
    return {"ok": True, "config": cfg, "count": len(pins)}


@app.delete("/api/pin/{ip}")
async def api_unpin(ip: str):
    cfg = C.load()
    rest = [p for p in (cfg.get("pinned") or []) if p.get("ip") != ip]
    cfg = C.save({"pinned": rest})
    _refresh_shown()
    return {"ok": True, "config": cfg}


@app.post("/api/proxmox/test")
async def api_proxmox_test(body: dict = Body(...)):
    """Proxmox-Zugang prüfen. Leeres Secret nutzt das gespeicherte."""
    from . import integrations
    conf = dict(body)
    if not (conf.get("token_secret") or "").strip():
        conf["token_secret"] = (C.load().get("proxmox") or {}).get("token_secret", "")
    return await integrations.test_proxmox(conf)


@app.get("/api/uptime/{ip}")
async def api_uptime(ip: str, buckets: int = 24):
    """Erreichbarkeits-Verlauf eines Geräts (Standard: 24 Stunden-Abschnitte)."""
    from . import uptime
    n = max(6, min(48, int(buckets or 24)))
    return uptime.history(ip, n)


@app.put("/api/dashboards/{dash_id}/share")
async def api_dash_share(dash_id: str, request: Request, body: dict = Body(...)):
    """Ein Dashboard für andere Benutzer freigeben (Liste von Benutzer-IDs)."""
    from . import users as UU
    cfg = C.load()
    dash = next((d for d in cfg.get("dashboards", []) if d["id"] == dash_id), None)
    if not dash:
        return JSONResponse({"ok": False, "errors": ["Dashboard nicht gefunden."]},
                            status_code=404)
    me = current_user(request)
    if not UU.may_edit(dash, me):
        return JSONResponse({"ok": False, "errors": ["Nur der Besitzer darf teilen."]},
                            status_code=403)
    known = {u["id"] for u in UU.all_users()}
    owner = dash.get("owner") or (me or {}).get("id", "")
    wanted = [str(x) for x in (body.get("shared") or [])
              if str(x) in known and str(x) != owner]
    dashboards = [{**d, "shared": wanted} if d["id"] == dash_id else d
                  for d in cfg.get("dashboards", [])]
    result, code = _apply({"dashboards": dashboards})
    if code == 200:
        # Nur die wirklich neu Hinzugekommenen benachrichtigen – und bei einer
        # zurückgenommenen Freigabe den alten Hinweis wieder einsammeln.
        before = set(dash.get("shared") or [])
        who = (me or {}).get("name", "Jemand")
        for uid in set(wanted) - before:
            UU.add_notice(uid, "share", dash=dash_id,
                          dash_name=dash.get("name", ""), by=who)
        for uid in before - set(wanted):
            UU.drop_notices_for_dash(dash_id, uid)
    return JSONResponse(result, status_code=code)


@app.get("/api/notices")
async def api_notices(request: Request):
    """Eigene Hinweise abholen (z. B. neue Freigaben)."""
    from . import users as UU
    me = current_user(request)
    return {"notices": UU.notices_for(me["id"]) if me else []}


@app.delete("/api/notices/{nid}")
async def api_notice_ack(nid: str, request: Request):
    from . import users as UU
    me = current_user(request)
    if me:
        UU.drop_notice(me["id"], nid)
    return {"ok": True}


@app.get("/api/dashboards/{dash_id}/background")
async def api_dash_bg_get(dash_id: str):
    """Eigenes Hintergrundbild eines Dashboards ausliefern."""
    safe = re.sub(r"[^a-zA-Z0-9_-]", "", dash_id)[:32]
    found = next(iter(C.DATA_DIR.glob(f"bg-{safe}.*")), None)
    if not found:
        return JSONResponse({"ok": False}, status_code=404)
    return FileResponse(found, headers={"Cache-Control": "no-cache"})


@app.post("/api/dashboards/{dash_id}/background")
async def api_dash_bg_set(dash_id: str, body: dict = Body(...)):
    """Hintergrundbild für genau ein Dashboard hinterlegen (Data-URL)."""
    cfg = C.load()
    if not any(d["id"] == dash_id for d in cfg.get("dashboards", [])):
        return JSONResponse({"ok": False, "errors": ["Dashboard nicht gefunden."]},
                            status_code=404)
    safe = re.sub(r"[^a-zA-Z0-9_-]", "", dash_id)[:32]
    data = str(body.get("data", ""))
    m = re.match(r"^data:([^;]+);base64,(.+)$", data, re.S)
    if not m or m.group(1) not in _BG_TYPES:
        return JSONResponse({"ok": False,
            "errors": ["Bitte ein Bild (PNG, JPG, WEBP, GIF, SVG)."]}, status_code=400)
    try:
        raw = base64.b64decode(m.group(2), validate=True)
    except (ValueError, binascii.Error):
        return JSONResponse({"ok": False, "errors": ["Bild ließ sich nicht lesen."]},
                            status_code=400)
    if len(raw) > 6_000_000:
        return JSONResponse({"ok": False, "errors": ["Bild ist zu groß (max. 6 MB)."]},
                            status_code=400)
    C.DATA_DIR.mkdir(parents=True, exist_ok=True)
    for old in C.DATA_DIR.glob(f"bg-{safe}.*"):
        old.unlink(missing_ok=True)
    (C.DATA_DIR / f"bg-{safe}.{_BG_TYPES[m.group(1)]}").write_bytes(raw)
    dashboards = [{**d, "background": "image"} if d["id"] == dash_id else d
                  for d in cfg.get("dashboards", [])]
    result, code = _apply({"dashboards": dashboards})
    return JSONResponse(result, status_code=code)


@app.delete("/api/dashboards/{dash_id}/background")
async def api_dash_bg_del(dash_id: str):
    """Eigenes Bild entfernen – das Dashboard erbt dann wieder den globalen Look."""
    safe = re.sub(r"[^a-zA-Z0-9_-]", "", dash_id)[:32]
    for old in C.DATA_DIR.glob(f"bg-{safe}.*"):
        old.unlink(missing_ok=True)
    cfg = C.load()
    dashboards = [{**d, "background": ""} if d["id"] == dash_id else d
                  for d in cfg.get("dashboards", [])]
    result, code = _apply({"dashboards": dashboards})
    return JSONResponse(result, status_code=code)


def is_offline() -> bool:
    """Läuft Netboard im Offline-Betrieb? Dann unterbleibt jede Anfrage nach außen."""
    return C.load().get("net_mode") == "offline"


@app.get("/api/net/status")
async def api_net_status(probe: bool = False):
    """Betriebsart und – auf Wunsch – ein kurzer Test, ob das Internet erreichbar ist.

    Der Test fragt nur die GitHub-API an, die Netboard ohnehin für Updates nutzt.
    Im Offline-Betrieb wird gar nicht erst gefragt.
    """
    cfg = C.load()
    mode = cfg.get("net_mode", "online")
    out = {"mode": mode, "offline": mode == "offline"}
    if not probe or mode == "offline":
        return out
    try:
        async with httpx.AsyncClient(timeout=4.0, follow_redirects=True) as c:
            r = await c.get("https://api.github.com/", headers={"User-Agent": "Netboard"})
        out["online"] = r.status_code < 500
    except Exception:
        out["online"] = False
    return out


@app.get("/api/2fa/qr.svg")
async def api_2fa_qr(request: Request, secret: str = "", account: str = ""):
    """QR-Code für die Authenticator-App – hier im Haus erzeugt, ohne Fremddienst.

    Damit funktioniert die Einrichtung auch ohne Internet, und das Geheimnis
    verlässt den Server nie.
    """
    from . import totp as T
    me = current_user(request)
    auth = C.load().get("auth") or {}
    if auth.get("enabled") and not me:
        return JSONResponse({"ok": False}, status_code=403)
    sec = re.sub(r"[^A-Za-z0-9]", "", secret)[:64]
    if not sec:
        return JSONResponse({"ok": False}, status_code=400)
    name = (account or (me or {}).get("name") or "netboard")[:48]
    uri = T.provisioning_uri(sec, name)
    try:
        import segno
        buf = io.BytesIO()
        segno.make(uri, error="m").save(buf, kind="svg", scale=5, border=2,
                                        dark="#111827", light=None)
        return Response(buf.getvalue(), media_type="image/svg+xml",
                        headers={"Cache-Control": "no-store"})
    except Exception:
        return JSONResponse({"ok": False, "error": "QR-Code nicht erzeugbar."},
                            status_code=500)


_VER_CACHE: dict = {"at": 0, "data": None}


@app.get("/api/version")
async def api_version(force: bool = False):
    """Eigene Fassung, Codename, Änderungsprotokoll – und ob es Neues gibt."""
    from . import release as R
    from . import selfupdate as SU
    cfg = C.load()
    base = {"ok": True, "version": R.VERSION, "codename": R.CODENAME,
            "repo": R.REPO, "current": R.current(), "changelog": R.CHANGELOG[:6],
            "update": False, "latest": None,
            "dismissed": cfg.get("update_seen", "")}
    if cfg.get("net_mode") == "offline":
        return {**base, "note": "Offline-Betrieb – es wird nicht nach Updates gesehen.",
                "offline": True}
    if not cfg.get("update_check", True):
        return {**base, "note": "Die Update-Prüfung ist ausgeschaltet."}
    now = time.time()
    if not force and _VER_CACHE["data"] and now - _VER_CACHE["at"] < 6 * 3600:
        return {**base, **_VER_CACHE["data"]}
    got = await SU.latest()
    if not got.get("ok"):
        return {**base, "ok": False, "error": got.get("error", "")}
    tag = got.get("latest")
    out = {"latest": tag, "update": bool(tag and SU.is_newer(tag)),
           "url": got.get("url", ""), "tarball": got.get("tarball", ""),
           "published": got.get("published", ""),
           "release_name": got.get("name", ""),
           "release_notes": got.get("body", ""), "note": got.get("note", "")}
    _VER_CACHE.update({"at": now, "data": out})
    return {**base, **out}


@app.post("/api/update/dismiss")
async def api_update_dismiss(body: dict = Body(default={})):
    """Eine angebotene Fassung ausblenden – bis eine noch neuere erscheint."""
    C.save({"update_seen": str(body.get("version") or "")[:32]})
    return {"ok": True}


@app.get("/api/update/status")
async def api_update_status():
    from . import selfupdate as SU
    return SU.STATE


@app.post("/api/update/apply")
async def api_update_apply(request: Request, body: dict = Body(default={})):
    """Die neue Fassung einspielen. Nur für Verwalter."""
    from . import selfupdate as SU
    deny = _need_admin(request)
    if deny:
        return deny
    if is_offline():
        return JSONResponse({"ok": False, "errors": [
            "Offline-Betrieb: Für ein Update bitte vorübergehend auf „Online“ stellen."]},
            status_code=400)
    if SU.STATE.get("running"):
        return {"ok": True, "already": True}
    info = await SU.latest()
    tag = str(body.get("version") or info.get("latest") or "")
    if not tag:
        return JSONResponse({"ok": False, "errors": ["Keine Veröffentlichung gefunden."]},
                            status_code=400)
    if not SU.is_newer(tag):
        return JSONResponse({"ok": False, "errors": ["Diese Fassung läuft bereits."]},
                            status_code=400)
    asyncio.create_task(SU.run(tag, info.get("tarball", "")))
    return {"ok": True, "started": True, "version": tag}


# --- Wake-on-LAN --------------------------------------------------------------
@app.post("/api/wol/{ip}")
async def api_wol(ip: str, body: dict = Body(default={})):
    """Ein Gerät per Magic Packet wecken. MAC kommt aus dem Body oder wird aus
    dem letzten Scan bzw. den festgesetzten Geräten ermittelt."""
    from . import wol
    mac = str(body.get("mac") or "").strip()
    if not mac:
        # Serverseitig nachschlagen: erst laufender Scan, dann festgesetzte.
        for d in (STATE.get("found") or []):
            if d.get("ip") == ip and d.get("mac"):
                mac = d["mac"]; break
        if not mac:
            for p in (C.load().get("pinned") or []):
                if p.get("ip") == ip and p.get("mac"):
                    mac = p["mac"]; break
    if not mac:
        return JSONResponse({"ok": False,
            "error": "Keine MAC-Adresse bekannt – nach einem Scan erneut versuchen."},
            status_code=400)
    broadcasts = ["255.255.255.255"]
    sb = wol.subnet_broadcast(ip)
    if sb:
        broadcasts.append(sb)
    try:
        sent = wol.send_magic_packet(mac, broadcasts)
    except ValueError:
        return JSONResponse({"ok": False, "error": "Ungültige MAC-Adresse."}, status_code=400)
    return {"ok": sent > 0, "mac": mac, "sent": sent}


# --- OS-/Update-Prüfung per SSH ---------------------------------------------
@app.post("/api/probe/{ip}")
async def api_probe(ip: str, body: dict = Body(default={})):
    """Ein Gerät jetzt prüfen. Zugangsdaten aus dem Body oder – wenn leer – die
    gespeicherten. Auf Wunsch (save) werden sie verschlüsselt hinterlegt."""
    from . import osprobe
    cfg = C.load()
    stored = {p["ip"]: p for p in (cfg.get("probes") or [])}.get(ip, {})
    user = (body.get("user") or stored.get("user") or "").strip()
    password = body.get("password") or stored.get("password") or ""
    if not user or not password:
        return JSONResponse({"ok": False, "error": "Benutzer und Passwort nötig."},
                            status_code=400)
    if body.get("save"):
        others = [p for p in (cfg.get("probes") or []) if p.get("ip") != ip]
        C.save(C.validate({"probes": [*others, {"ip": ip, "user": user,
                "password": password, "enabled": True}]})[0])
    result = await osprobe.probe(ip, user, password)
    STATE["probes_result"][ip] = result
    return result


@app.post("/api/probe/{ip}/update/start")
async def api_probe_update_start(ip: str, body: dict = Body(default={})):
    """Update im Hintergrund starten. Die Ausgabe sammelt sich in STATE und wird
    per /update/log abgefragt (robuster als Streaming durch die Middleware)."""
    from . import osprobe
    cfg = C.load()
    stored = {p["ip"]: p for p in (cfg.get("probes") or [])}.get(ip, {})
    user = (stored.get("user") or "").strip()
    password = stored.get("password") or ""
    if not user or not password:
        return JSONResponse({"ok": False,
            "error": "Kein SSH-Zugang hinterlegt – erst „System prüfen“ mit „merken“."},
            status_code=400)
    rec = STATE["updates"].get(ip)
    if rec and rec.get("running"):
        return {"started": True, "already": True}
    os_id = str(body.get("os_id") or (STATE["probes_result"].get(ip) or {}).get("id", ""))
    STATE["updates"][ip] = {"lines": [], "running": True, "ok": None, "started": time.time()}

    async def worker():
        rec = STATE["updates"][ip]
        try:
            async for ev in osprobe.run_update_stream(ip, user, password, os_id):
                if ev.get("line") is not None:
                    rec["lines"].append(ev["line"])
                    del rec["lines"][:-800]        # Log begrenzen
                if ev.get("done") is not None:
                    rec["ok"] = bool(ev["ok"])
            if rec.get("ok"):
                STATE["probes_result"][ip] = await osprobe.probe(ip, user, password)
        except Exception as exc:                    # nie hängen bleiben
            rec["lines"].append(f"Fehler: {type(exc).__name__}")
            rec["ok"] = False
        finally:
            rec["running"] = False

    asyncio.create_task(worker())
    return {"started": True}


@app.get("/api/probe/{ip}/update/log")
async def api_probe_update_log(ip: str):
    """Aktuellen Update-Log + Status abfragen (fürs Live-Mitlesen)."""
    rec = STATE["updates"].get(ip)
    if not rec:
        return {"lines": [], "running": False, "ok": None}
    out = {"lines": rec["lines"], "running": rec["running"], "ok": rec["ok"]}
    if not rec["running"] and rec.get("ok"):
        out["probe"] = STATE["probes_result"].get(ip)
    return out


@app.post("/api/probe/{ip}/update")
async def api_probe_update(ip: str, body: dict = Body(default={})):
    """Updates auf dem Gerät tatsächlich einspielen. Nutzt den hinterlegten
    SSH-Zugang. Bei aktivem Login schützt die Middleware diese Route."""
    from . import osprobe
    cfg = C.load()
    stored = {p["ip"]: p for p in (cfg.get("probes") or [])}.get(ip, {})
    user = (stored.get("user") or "").strip()
    password = stored.get("password") or ""
    if not user or not password:
        return JSONResponse({"ok": False,
            "error": "Kein SSH-Zugang hinterlegt – erst „System prüfen“ mit „merken“."},
            status_code=400)
    os_id = str(body.get("os_id") or (STATE["probes_result"].get(ip) or {}).get("id", ""))
    result = await osprobe.run_update(ip, user, password, os_id)
    if result.get("ok"):
        STATE["probes_result"][ip] = await osprobe.probe(ip, user, password)
        result["probe"] = STATE["probes_result"][ip]
    return result


@app.delete("/api/probes/{ip}")
async def api_probe_del(ip: str):
    cfg = C.load()
    rest = [p for p in (cfg.get("probes") or []) if p.get("ip") != ip]
    C.save({"probes": rest})
    STATE["probes_result"].pop(ip, None)
    return {"ok": True}


async def _probe_loop() -> None:
    """Eingerichtete Geräte in Ruhe im Hintergrund frisch halten."""
    from . import osprobe
    while True:
        await asyncio.sleep(30)
        for p in (C.load().get("probes") or []):
            if p.get("enabled") and p.get("user") and p.get("password"):
                STATE["probes_result"][p["ip"]] = await osprobe.probe(
                    p["ip"], p["user"], p["password"])
        await asyncio.sleep(1800)   # danach alle 30 Minuten


# --- Sichern: Export / Import ------------------------------------------------
@app.get("/api/export")
async def api_export():
    """Gesamte Einstellung als JSON zum Herunterladen. Das vSphere-Passwort
    bleibt draußen – ein Backup soll man gefahrlos weitergeben können."""
    cfg = dict(C.load())
    vs = dict(cfg.get("vsphere") or {})
    vs["password"] = ""
    cfg["vsphere"] = vs
    px = dict(cfg.get("proxmox") or {})
    px["token_secret"] = ""
    cfg["proxmox"] = px
    au = dict(cfg.get("auth") or {})
    au.pop("password_hash", None)
    au["enabled"] = False   # Login-Zustand nicht mitexportieren
    cfg["auth"] = au
    if isinstance(cfg.get("probes"), list):
        cfg["probes"] = [{**p, "password": ""} for p in cfg["probes"]]
    # Konten gehören nicht in ein Backup: Passwort-Hashes und 2FA-Geheimnisse
    # sollen eine Datei, die man weitergibt, niemals enthalten.
    cfg.pop("users", None)
    cfg.pop("notices", None)      # Hinweise sind persönlich, nicht Teil einer Sicherung
    return JSONResponse({"netboard": 1, "config": cfg}, headers={
        "Content-Disposition": 'attachment; filename="netboard-config.json"'})


@app.post("/api/import")
async def api_import(body: dict = Body(...)):
    """Eine zuvor exportierte Einstellung übernehmen. Alles läuft durch die
    normale Prüfung; das aktuelle vSphere-Passwort bleibt erhalten, wenn das
    Backup keines enthält."""
    incoming = body.get("config") if isinstance(body.get("config"), dict) else body
    if not isinstance(incoming, dict):
        return JSONResponse({"ok": False, "errors": ["Kein gültiges Backup."]},
                            status_code=400)
    patch = {k: v for k, v in incoming.items() if k in C.DEFAULTS}
    clean, errs = C.validate(patch)
    if errs:
        return JSONResponse({"ok": False, "errors": errs}, status_code=400)
    try:
        cfg = C.save(clean)
    except RuntimeError as exc:
        return JSONResponse({"ok": False, "errors": [str(exc)]}, status_code=500)
    asyncio.create_task(run_scan())
    return {"ok": True, "config": cfg}


# --- Dashboards --------------------------------------------------------------
@app.post("/api/dashboards")
async def api_dash_add(request: Request, body: dict = Body(...)):
    cfg = C.load()
    entry = {"name": body.get("name", "").strip() or "Neues Dashboard",
             "auto_add": bool(body.get("auto_add", True)),
             "refresh": body.get("refresh", 0),
             "layout": body.get("layout", "cards"),
             "tile_style": body.get("tile_style", "outline"),
             "label_mode": body.get("label_mode", "full"),
             "width": body.get("width", "normal"),
             "order": body.get("order", []),
             "group": body.get("group", "none"),
             "sort": body.get("sort", "natural"),
             "icon_shape": body.get("icon_shape", "rounded"),
             "columns": body.get("columns", 0),
             "only_online": bool(body.get("only_online", False)),
             "show_stats": bool(body.get("show_stats", True)),
             "show_probe": bool(body.get("show_probe", True)),
             # Wer es anlegt, besitzt es – so bleibt es privat, bis geteilt wird.
             "owner": (current_user(request) or {}).get("id", ""), "shared": [],
             "members": body.get("members", []), "hidden": []}
    result, code = _apply({"dashboards": [*cfg.get("dashboards", []), entry]})
    if code == 200:   # die neue Kennung zurückgeben, damit die Sicht umschalten kann
        known = {d["id"] for d in cfg.get("dashboards", [])}
        fresh = [d for d in result["config"]["dashboards"] if d["id"] not in known]
        result["created"] = fresh[0] if fresh else None
    return JSONResponse(result, status_code=code)


@app.put("/api/dashboards/{dash_id}")
async def api_dash_update(dash_id: str, request: Request, body: dict = Body(...)):
    from . import users as UU
    cfg = C.load()
    dashboards = cfg.get("dashboards", [])
    target = next((d for d in dashboards if d["id"] == dash_id), None)
    if not target:
        return JSONResponse({"ok": False, "errors": ["Dashboard nicht gefunden."]},
                            status_code=404)
    me = current_user(request)
    if not UU.may_edit(target, me):
        return JSONResponse({"ok": False,
                             "errors": ["Dieses Dashboard gehört jemand anderem."]},
                            status_code=403)
    # Den Besitzer darf nur ein Verwalter umschreiben.
    if "owner" in body and me and me.get("role") != "admin":
        body = {k: v for k, v in body.items() if k != "owner"}
    # Muss jedes Feld enthalten, das ein Dashboard kennt – sonst verschwinden
    # Änderungen still. Wird von test_dashboard_fields abgedeckt.
    fields = ("name", "background", "bg_grad", "tile_size", "scheme_filter",
              "show_ping", "hide_empty", "auto_add", "show_stats", "show_probe", "seen",
              "refresh", "layout",
              "tile_style", "label_mode", "width", "order", "group", "sort",
              "icon_shape", "columns", "only_online", "members", "hidden",
              "folders", "assign", "folder_view", "owner")
    updated = [{**d, **{k: v for k, v in body.items() if k in fields}}
               if d["id"] == dash_id else d for d in dashboards]
    result, code = _apply({"dashboards": updated})
    return JSONResponse(result, status_code=code)


@app.delete("/api/dashboards/{dash_id}")
async def api_dash_del(dash_id: str):
    cfg = C.load()
    rest = [d for d in cfg.get("dashboards", []) if d["id"] != dash_id]
    if len(rest) == len(cfg.get("dashboards", [])):
        return JSONResponse({"ok": False, "errors": ["Dashboard nicht gefunden."]},
                            status_code=404)
    if not rest:
        return JSONResponse(
            {"ok": False, "errors": ["Das letzte Dashboard lässt sich nicht löschen."]},
            status_code=400)
    patch: dict = {"dashboards": rest}
    # Eigenes Hintergrundbild dieses Dashboards mit entsorgen.
    safe = re.sub(r"[^a-zA-Z0-9_-]", "", dash_id)[:32]
    for old_bg in C.DATA_DIR.glob(f"bg-{safe}.*"):
        old_bg.unlink(missing_ok=True)
    # Kachel-Designs, die nur für dieses Dashboard galten, mit entfernen –
    # sonst bleiben unsichtbare Reste in der Konfiguration liegen.
    styles = cfg.get("tile_styles") or {}
    pruned = {k: v for k, v in styles.items() if not k.startswith(f"{dash_id}@")}
    if len(pruned) != len(styles):
        patch["tile_styles"] = pruned
    result, code = _apply(patch)
    return JSONResponse(result, status_code=code)


@app.post("/api/dashboards/order")
async def api_dash_order(body: dict = Body(...)):
    """Neue Reihenfolge. Unbekannte Kennungen werden ignoriert, fehlende
    hinten angehängt – so kann Ziehen nie ein Dashboard verschlucken."""
    cfg = C.load()
    dashboards = cfg.get("dashboards", [])
    by = {d["id"]: d for d in dashboards}
    order = [by[i] for i in (body.get("ids") or []) if i in by]
    order += [d for d in dashboards if d not in order]
    result, code = _apply({"dashboards": order})
    return JSONResponse(result, status_code=code)


@app.post("/api/dashboards/{dash_id}/default")
async def api_dash_default(dash_id: str):
    """Startansicht festlegen – gilt auf jedem Gerät."""
    cfg = C.load()
    if not any(d["id"] == dash_id for d in cfg.get("dashboards", [])):
        return JSONResponse({"ok": False, "errors": ["Dashboard nicht gefunden."]},
                            status_code=404)
    result, code = _apply({"default_dashboard": dash_id})
    return JSONResponse(result, status_code=code)


# --- Eigene Schriftart --------------------------------------------------------
_FONT_TYPES = {
    "woff2": "font/woff2", "woff": "font/woff",
    "ttf": "font/ttf", "otf": "font/otf",
}


@app.post("/api/font")
async def api_font_upload(request: Request):
    """Eine eigene Schriftdatei hinterlegen (woff2/woff/ttf/otf, max. 3 MB)."""
    raw = await request.body()
    if not raw:
        return JSONResponse({"ok": False, "errors": ["Keine Datei erhalten."]},
                            status_code=400)
    if len(raw) > 3_000_000:
        return JSONResponse({"ok": False, "errors": ["Die Datei ist größer als 3 MB."]},
                            status_code=400)
    ext = (request.query_params.get("ext") or "woff2").lower().lstrip(".")
    if ext not in _FONT_TYPES:
        return JSONResponse(
            {"ok": False, "errors": ["Nur woff2, woff, ttf oder otf."]}, status_code=400)
    slot = "monofont" if request.query_params.get("slot") == "mono" else "font"
    # Alte Datei(en) entfernen, damit je Platz genau eine Schrift existiert.
    for old in C.DATA_DIR.glob(f"{slot}.*"):
        try:
            old.unlink()
        except OSError:
            pass
    (C.DATA_DIR / f"{slot}.{ext}").write_bytes(raw)
    patch = {"_fontv": int(time.time())}
    if slot == "font":
        patch["font"] = "custom"
    else:
        patch["mono_custom"] = True
    cfg = C.save(patch)
    return {"ok": True, "config": cfg}


@app.delete("/api/font")
async def api_font_delete(request: Request):
    """Eigene Schrift entfernen und auf die Systemschrift zurückfallen."""
    slot = "monofont" if request.query_params.get("slot") == "mono" else "font"
    for old in C.DATA_DIR.glob(f"{slot}.*"):
        try:
            old.unlink()
        except OSError:
            pass
    cfg = C.load()
    patch: dict = {"_fontv": int(time.time())}
    if slot == "font" and cfg.get("font") == "custom":
        patch["font"] = "system"
    if slot == "monofont":
        patch["mono_custom"] = False
    return {"ok": True, "config": C.save(patch)}


@app.get("/font.css")
async def api_font_css():
    """Stylesheet für die hinterlegten Schriften (Fließtext und dicktengleich)."""
    v = C.load().get("_fontv", 1)
    out = []
    for slot, family in (("font", "Netboard Custom"), ("monofont", "Netboard Mono")):
        found = next(iter(C.DATA_DIR.glob(f"{slot}.*")), None)
        if not found:
            continue
        ext = found.suffix.lstrip(".").lower()
        out.append(f"@font-face{{font-family:'{family}';"
                   f"src:url('/font.file?slot={slot}&v={v}') "
                   f"format('{'truetype' if ext == 'ttf' else ext}');"
                   "font-display:swap;font-weight:100 900}")
    return Response("".join(out), media_type="text/css",
                    headers={"Cache-Control": "no-cache"})


@app.get("/font.file")
async def api_font_file(slot: str = "font"):
    slot = "monofont" if slot == "monofont" else "font"
    found = next(iter(C.DATA_DIR.glob(f"{slot}.*")), None)
    if not found:
        return JSONResponse({"ok": False}, status_code=404)
    ext = found.suffix.lstrip(".").lower()
    return Response(found.read_bytes(),
                    media_type=_FONT_TYPES.get(ext, "application/octet-stream"),
                    headers={"Cache-Control": "public, max-age=86400"})


# --- Schnelllinks ------------------------------------------------------------
@app.post("/api/links")
async def api_link_add(body: dict = Body(...)):
    """Neuen Schnelllink anlegen. Gibt den angelegten Eintrag zurück, damit die
    Oberfläche ihn direkt einsortieren kann."""
    cfg = C.load()
    entry = {"name": body.get("name", ""), "url": body.get("url", ""),
             "icon": body.get("icon", ""), "color": body.get("color", ""),
             "favicon": body.get("favicon", False), "boards": body.get("boards", [])}
    result, code = _apply({"links": [*cfg.get("links", []), entry]})
    if code == 200:
        known = {l["id"] for l in cfg.get("links", [])}
        fresh = [l for l in result["config"]["links"] if l["id"] not in known]
        result["created"] = fresh[0] if fresh else None
    return JSONResponse(result, status_code=code)


@app.put("/api/links/{link_id}")
async def api_link_update(link_id: str, body: dict = Body(...)):
    cfg = C.load()
    links = cfg.get("links", [])
    if not any(l["id"] == link_id for l in links):
        return JSONResponse({"ok": False, "errors": ["Link nicht gefunden."]},
                            status_code=404)
    fields = ("name", "url", "icon", "color", "boards", "favicon")
    updated = [{**l, **{k: v for k, v in body.items() if k in fields}}
               if l["id"] == link_id else l for l in links]
    result, code = _apply({"links": updated})
    return JSONResponse(result, status_code=code)


@app.delete("/api/links/{link_id}")
async def api_link_del(link_id: str):
    cfg = C.load()
    rest = [l for l in cfg.get("links", []) if l["id"] != link_id]
    if len(rest) == len(cfg.get("links", [])):
        return JSONResponse({"ok": False, "errors": ["Link nicht gefunden."]},
                            status_code=404)
    result, code = _apply({"links": rest})
    return JSONResponse(result, status_code=code)


# --- Geräte ------------------------------------------------------------------
@app.post("/api/devices")
async def api_device_add(body: dict = Body(...)):
    """Eigenes Gerät anlegen."""
    cfg = C.load()
    entry = {"name": body.get("name", ""), "ip": str(body.get("ip", "")).strip(),
             "ports": body.get("ports", "")}
    if any(m["ip"] == entry["ip"] for m in cfg.get("manual_devices", [])):
        return JSONResponse(
            {"ok": False, "errors": [f"„{entry['ip']}“ ist bereits als eigenes "
                                     "Gerät angelegt."]}, status_code=400)
    result, code = _apply({"manual_devices": [*cfg.get("manual_devices", []), entry]},
                          rescan=True)
    return JSONResponse(result, status_code=code)


@app.delete("/api/devices/{device_id}")
async def api_device_del(device_id: str):
    cfg = C.load()
    rest = [m for m in cfg.get("manual_devices", []) if m.get("id") != device_id]
    if len(rest) == len(cfg.get("manual_devices", [])):
        return JSONResponse({"ok": False, "errors": ["Gerät nicht gefunden."]},
                            status_code=404)
    result, code = _apply({"manual_devices": rest}, rescan=True)
    return JSONResponse(result, status_code=code)


@app.post("/api/devices/hide")
async def api_device_hide(body: dict = Body(...)):
    """Überall deaktivieren – wird künftig nicht mal mehr abgefragt."""
    ip = str(body.get("ip", "")).strip()
    cfg = C.load()
    result, code = _apply({"hidden": [*cfg.get("hidden", []), ip]})
    return JSONResponse(result, status_code=code)


@app.post("/api/devices/unhide")
async def api_device_unhide(body: dict = Body(...)):
    ip = str(body.get("ip", "")).strip()
    cfg = C.load()
    result, code = _apply({"hidden": [x for x in cfg.get("hidden", []) if x != ip]},
                          rescan=True)
    return JSONResponse(result, status_code=code)


# --- Favicons ----------------------------------------------------------------
@app.get("/icon/{ip}/{port}")
async def api_icon(ip: str, port: int):
    entry = ICONS.get(f"{ip}:{port}")
    if not entry:
        return Response(status_code=404)
    ctype, data = entry
    return Response(content=data, media_type=ctype, headers={
        "Cache-Control": "public, max-age=86400",
        "ETag": hashlib.md5(data).hexdigest()[:16],
    })


# --- SSH ---------------------------------------------------------------------
@app.get("/api/ssh/user")
async def api_ssh_user(host: str):
    """Gemerkter Benutzername – nie ein Passwort."""
    return {"user": (C.load().get("ssh_users") or {}).get(host, "")}


def _remember_user(host: str, user: str) -> None:
    cfg = C.load()
    users = dict(cfg.get("ssh_users") or {})
    if users.get(host) == user:
        return
    users[host] = user
    clean, errs = C.validate({"ssh_users": users})
    if not errs:
        C.save(clean)


@app.websocket("/ws/ssh")
async def ws_ssh(ws: WebSocket):
    await sshgw.bridge(ws, C.load(), _remember_user)


@app.get("/ssh")
async def ssh_page():
    return FileResponse(STATIC / "ssh.html", headers={"Cache-Control": "no-cache"})


# --- Eigenes Aussehen: CSS und Hintergrundbild -------------------------------
_CSS_FILE = C.DATA_DIR / "custom.css"
_BG_GLOB = "background.*"
_BG_TYPES = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp",
             "image/gif": "gif", "image/svg+xml": "svg"}


def _bg_file():
    files = sorted(C.DATA_DIR.glob(_BG_GLOB))
    return files[0] if files else None


@app.get("/custom.css")
async def custom_css():
    """Eigenes CSS der Seite. Fehlt es, kommt leeres CSS zurück – dann greift
    schlicht das Standard-Design."""
    if _CSS_FILE.exists():
        return FileResponse(_CSS_FILE, media_type="text/css",
                            headers={"Cache-Control": "no-cache"})
    return Response("/* kein eigenes CSS */", media_type="text/css")


@app.get("/api/css")
async def api_css_get():
    return {"css": _CSS_FILE.read_text("utf-8") if _CSS_FILE.exists() else ""}


@app.post("/api/css")
async def api_css_set(body: dict = Body(...)):
    css = str(body.get("css", ""))
    if len(css) > 200_000:
        return JSONResponse({"ok": False, "errors": ["CSS ist zu groß (max. 200 kB)."]},
                            status_code=400)
    C.DATA_DIR.mkdir(parents=True, exist_ok=True)
    _CSS_FILE.write_text(css, "utf-8")
    return {"ok": True}


@app.delete("/api/css")
async def api_css_del():
    _CSS_FILE.unlink(missing_ok=True)
    return {"ok": True}


@app.get("/api/background")
async def api_bg_get():
    f = _bg_file()
    if not f:
        return JSONResponse({"ok": False}, status_code=404)
    return FileResponse(f, headers={"Cache-Control": "no-cache"})


@app.post("/api/background")
async def api_bg_set(body: dict = Body(...)):
    """Hintergrundbild als Data-URL entgegennehmen (der Browser liest die Datei
    und schickt sie so). Wir prüfen Typ und Größe, dann liegt sie im Volume."""
    data = str(body.get("data", ""))
    m = re.match(r"^data:([^;]+);base64,(.+)$", data, re.S)
    if not m or m.group(1) not in _BG_TYPES:
        return JSONResponse({"ok": False,
            "errors": ["Bitte ein Bild (PNG, JPG, WEBP, GIF, SVG)."]}, status_code=400)
    try:
        raw = base64.b64decode(m.group(2), validate=True)
    except (ValueError, binascii.Error):
        return JSONResponse({"ok": False, "errors": ["Bild ließ sich nicht lesen."]},
                            status_code=400)
    if len(raw) > 6_000_000:
        return JSONResponse({"ok": False, "errors": ["Bild ist zu groß (max. 6 MB)."]},
                            status_code=400)
    C.DATA_DIR.mkdir(parents=True, exist_ok=True)
    for old in C.DATA_DIR.glob(_BG_GLOB):
        old.unlink(missing_ok=True)
    (C.DATA_DIR / f"background.{_BG_TYPES[m.group(1)]}").write_bytes(raw)
    cfg = C.save({"background": "image"})
    return {"ok": True, "config": cfg}


@app.delete("/api/background")
async def api_bg_del():
    for old in C.DATA_DIR.glob(_BG_GLOB):
        old.unlink(missing_ok=True)
    cfg = C.save({"background": "plain"})
    return {"ok": True, "config": cfg}


@app.get("/api/favicon")
async def api_favicon(url: str):
    """Favicon einer (Link-)Seite ausliefern – serverseitig geholt, gecacht.

    SSRF-Schutz: Es werden ausschließlich Hosts geholt, die als Schnelllink
    hinterlegt sind. So kann eine (versehentlich) öffentliche Instanz nicht als
    Sprungbrett missbraucht werden, um beliebige interne Dienste abzufragen.
    """
    import ipaddress
    from urllib.parse import urlsplit
    from . import enrich

    def _host(u: str) -> str:
        if not re.match(r"^https?://", u or ""):
            u = "http://" + (u or "")
        return (urlsplit(u).hostname or "").lower()

    want = _host(url)
    # Offline-Betrieb: nur Symbole aus dem eigenen Netz holen, nichts von außen.
    if is_offline():
        try:
            addr = ipaddress.ip_address(want)
            local = addr.is_private or addr.is_loopback or addr.is_link_local
        except ValueError:
            # Namen ohne Punkt (nas) oder mit Heimnetz-Endung gelten als lokal.
            local = ("." not in want) or want.endswith(
                (".local", ".lan", ".home", ".fritz.box", ".internal", ".home.arpa"))
        if not local:
            return JSONResponse({"ok": False, "offline": True}, status_code=404)
    allowed = {_host(l.get("url", "")) for l in (C.load().get("links") or [])}
    if not want or want not in allowed:
        return Response(status_code=404)

    res = await enrich.site_favicon(url)
    if not res:
        return Response(status_code=404)
    return Response(content=res[1], media_type=res[0],
                    headers={"Cache-Control": "max-age=86400"})


# --- Oberfläche --------------------------------------------------------------
@app.get("/")
async def index():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/favicon.ico")
async def favicon():
    return FileResponse(STATIC / "favicon.ico")


# xterm.js liegt lokal bei – die Seite lädt nichts aus dem Internet nach.
app.mount("/static", StaticFiles(directory=STATIC), name="static")
