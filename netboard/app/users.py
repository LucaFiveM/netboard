"""Benutzerkonten: Anlegen, Anmelden, Rollen und Zwei-Faktor-Anmeldung.

Netboard kennt zwei Rollen:

* ``admin`` – darf alles: Einstellungen, Suchlauf, Benutzer verwalten.
* ``user``  – sieht nur die eigenen und die mit ihm geteilten Dashboards und
  ändert nur seine eigenen Sachen.

Passwörter liegen als scrypt-Hash, 2FA-Geheimnisse verschlüsselt (secretstore).
Beides verlässt den Server nie – die API gibt nur Flags wie ``has_2fa`` heraus.
"""
from __future__ import annotations

import re
import secrets
import time
from typing import Any

from . import config as C
from . import secretstore as ss
from . import totp as T

ROLES = ("admin", "user")
NAME_RE = re.compile(r"^[a-zA-Z0-9._@ -]{2,32}$")


def _uid() -> str:
    return secrets.token_hex(4)


def all_users() -> list[dict[str, Any]]:
    return list(C.load().get("users") or [])


def find(uid: str) -> dict[str, Any] | None:
    return next((u for u in all_users() if u["id"] == uid), None)


def by_name(name: str) -> dict[str, Any] | None:
    low = (name or "").strip().lower()
    return next((u for u in all_users() if u["name"].lower() == low), None)


def public(u: dict[str, Any]) -> dict[str, Any]:
    """Fassung für die Oberfläche – ohne Hash, ohne 2FA-Geheimnis."""
    return {"id": u["id"], "name": u["name"], "role": u.get("role", "user"),
            "has_2fa": bool(u.get("totp_secret")),
            "recovery_left": len(u.get("recovery") or []),
            "created": u.get("created", 0)}


def create(name: str, password: str, role: str = "user") -> tuple[dict | None, list[str]]:
    name = (name or "").strip()
    if not NAME_RE.match(name):
        return None, ["Name: 2–32 Zeichen, Buchstaben, Ziffern, . _ @ - erlaubt."]
    if by_name(name):
        return None, ["Diesen Namen gibt es schon."]
    if len(password or "") < 8:
        return None, ["Das Passwort braucht mindestens 8 Zeichen."]
    if role not in ROLES:
        role = "user"
    user = {"id": _uid(), "name": name, "role": role,
            "password_hash": ss.hash_password(password),
            "totp_secret": "", "recovery": [], "created": int(time.time())}
    users = all_users() + [user]
    C.save({"users": users})
    return user, []


def update(uid: str, *, name: str | None = None, password: str | None = None,
           role: str | None = None) -> tuple[dict | None, list[str]]:
    users = all_users()
    idx = next((i for i, u in enumerate(users) if u["id"] == uid), None)
    if idx is None:
        return None, ["Benutzer nicht gefunden."]
    u = dict(users[idx])
    if name is not None:
        name = name.strip()
        if not NAME_RE.match(name):
            return None, ["Name: 2–32 Zeichen, Buchstaben, Ziffern, . _ @ - erlaubt."]
        other = by_name(name)
        if other and other["id"] != uid:
            return None, ["Diesen Namen gibt es schon."]
        u["name"] = name
    if password:
        if len(password) < 8:
            return None, ["Das Passwort braucht mindestens 8 Zeichen."]
        u["password_hash"] = ss.hash_password(password)
    if role in ROLES:
        # Der letzte Verwalter darf sich nicht selbst entmachten.
        admins = [x for x in users if x.get("role") == "admin"]
        if role != "admin" and len(admins) == 1 and admins[0]["id"] == uid:
            return None, ["Es muss mindestens ein Verwalter bleiben."]
        u["role"] = role
    users[idx] = u
    C.save({"users": users})
    return u, []


def delete(uid: str) -> tuple[bool, list[str]]:
    users = all_users()
    victim = next((u for u in users if u["id"] == uid), None)
    if not victim:
        return False, ["Benutzer nicht gefunden."]
    admins = [u for u in users if u.get("role") == "admin"]
    if victim.get("role") == "admin" and len(admins) <= 1:
        return False, ["Der letzte Verwalter lässt sich nicht löschen."]
    rest = [u for u in users if u["id"] != uid]
    # Dashboards des Gelöschten gehen an den ersten Verwalter über, damit
    # nichts unsichtbar im Nichts hängen bleibt.
    heir = next((u["id"] for u in rest if u.get("role") == "admin"), "")
    dashboards = []
    for d in C.load().get("dashboards") or []:
        d = dict(d)
        if d.get("owner") == uid:
            d["owner"] = heir
        d["shared"] = [s for s in (d.get("shared") or []) if s != uid]
        dashboards.append(d)
    C.save({"users": rest, "dashboards": dashboards})
    return True, []


def check_login(name: str, password: str) -> dict[str, Any] | None:
    u = by_name(name)
    if u and ss.verify_password(password, u.get("password_hash", "")):
        return u
    return None


# --- Zwei-Faktor --------------------------------------------------------------
def start_2fa(uid: str) -> tuple[dict | None, list[str]]:
    """Neues Geheimnis erzeugen und zurückgeben – noch nicht scharf geschaltet."""
    u = find(uid)
    if not u:
        return None, ["Benutzer nicht gefunden."]
    secret = T.new_secret()
    return {"secret": secret,
            "uri": T.provisioning_uri(secret, u["name"])}, []


def enable_2fa(uid: str, secret: str, given_code: str) -> tuple[list[str] | None, list[str]]:
    """Mit einem gültigen Code bestätigen und einschalten. Gibt Ersatzcodes zurück."""
    u = find(uid)
    if not u:
        return None, ["Benutzer nicht gefunden."]
    if not T.verify(secret, given_code):
        return None, ["Der Code stimmt nicht – bitte den aktuellen aus der App eingeben."]
    codes = T.new_recovery_codes()
    users = [{**x, "totp_secret": secret,
              "recovery": [ss.hash_password(c) for c in codes]}
             if x["id"] == uid else x for x in all_users()]
    C.save({"users": users})
    return codes, []


def disable_2fa(uid: str) -> tuple[bool, list[str]]:
    users = [{**x, "totp_secret": "", "recovery": []} if x["id"] == uid else x
             for x in all_users()]
    C.save({"users": users})
    return True, []


def check_2fa(uid: str, given: str) -> bool:
    """Einmalcode oder Ersatzcode prüfen. Ein Ersatzcode gilt genau einmal."""
    u = find(uid)
    if not u or not u.get("totp_secret"):
        return True                       # ohne 2FA ist nichts zu prüfen
    if T.verify(u["totp_secret"], given):
        return True
    rest = list(u.get("recovery") or [])
    for i, stored in enumerate(rest):
        if ss.verify_password(str(given or "").strip(), stored):
            rest.pop(i)                   # verbraucht
            users = [{**x, "recovery": rest} if x["id"] == uid else x
                     for x in all_users()]
            C.save({"users": users})
            return True
    return False


# --- Sichtbarkeit von Dashboards ---------------------------------------------
def visible_dashboards(dashboards: list[dict], user: dict | None) -> list[dict]:
    """Welche Dashboards darf dieser Benutzer sehen?

    Ohne Login (kein Benutzer) oder als Verwalter: alle. Sonst die eigenen, die
    geteilten – und die herrenlosen, damit nach einer Umstellung nichts
    plötzlich verschwindet.
    """
    if not user or user.get("role") == "admin":
        return dashboards
    uid = user["id"]
    return [d for d in dashboards
            if not d.get("owner") or d.get("owner") == uid
            or uid in (d.get("shared") or [])]


def may_edit(dash: dict, user: dict | None) -> bool:
    """Ändern darf: Verwalter, Eigentümer – und wer ein herrenloses Board hat."""
    if not user or user.get("role") == "admin":
        return True
    return not dash.get("owner") or dash.get("owner") == user["id"]


# --- Hinweise ("X hat ein Dashboard mit dir geteilt") -------------------------
MAX_NOTICES = 30


def add_notice(uid: str, kind: str, **fields: Any) -> None:
    """Einen Hinweis für einen Benutzer hinterlegen."""
    store = dict(C.load().get("notices") or {})
    rows = list(store.get(uid) or [])
    rows.append({"id": secrets.token_hex(4), "kind": kind,
                 "at": int(time.time()), **fields})
    store[uid] = rows[-MAX_NOTICES:]
    C.save({"notices": store})


def notices_for(uid: str) -> list[dict[str, Any]]:
    return list((C.load().get("notices") or {}).get(uid) or [])


def drop_notice(uid: str, nid: str) -> None:
    store = dict(C.load().get("notices") or {})
    rows = [n for n in (store.get(uid) or []) if n.get("id") != nid]
    if rows:
        store[uid] = rows
    else:
        store.pop(uid, None)
    C.save({"notices": store})


def drop_notices_for_dash(dash_id: str, uid: str) -> None:
    """Wird eine Freigabe zurückgenommen, verschwindet auch der Hinweis."""
    store = dict(C.load().get("notices") or {})
    rows = [n for n in (store.get(uid) or []) if n.get("dash") != dash_id]
    if rows:
        store[uid] = rows
    else:
        store.pop(uid, None)
    C.save({"notices": store})


def migrate_legacy() -> None:
    """Aus dem alten Einzel-Login ein richtiges Verwalterkonto machen."""
    cfg = C.load()
    if cfg.get("users"):
        return
    auth = cfg.get("auth") or {}
    if not auth.get("password_hash"):
        return
    C.save({"users": [{
        "id": _uid(), "name": auth.get("username") or "admin", "role": "admin",
        "password_hash": auth["password_hash"], "totp_secret": "", "recovery": [],
        "created": int(time.time())}]})
