"""SSH-Terminal im Browser: WebSocket <-> asyncssh.

Zwei bewusste Sperren:

1. Das Ziel muss in einem der eingerichteten Netze liegen. Sonst ließe sich
   Netboard als offener Sprungbrett-Proxy in beliebige Netze missbrauchen.
2. Passwörter werden nirgends gespeichert, nicht protokolliert und nur für
   den Aufbau der Sitzung durchgereicht. Gemerkt wird höchstens der
   Benutzername.

Der Dienst ist standardmäßig abgeschaltet (`ssh_enabled`). Netboard hat keine
eigene Anmeldung – wer die Oberfläche erreicht, erreicht auch diese Brücke.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
from typing import Any

import asyncssh
from fastapi import WebSocket, WebSocketDisconnect

# Bewusst knapp: ein Terminal, das nach dem Tab-Schließen weiterläuft, will niemand.
CONNECT_TIMEOUT = 12

# Statusmeldungen laufen über Schließcodes, nicht über den Datenstrom. Sonst
# könnte der Browser eine Fehlermeldung nicht von echter Ausgabe unterscheiden.
CLOSE_DENIED = 4003   # Ziel nicht erlaubt / abgeschaltet
CLOSE_FAILED = 4001   # Anmeldung oder Verbindung fehlgeschlagen
READY = "\x01"       # markiert: die Sitzung steht


async def _fail(ws: WebSocket, code: int, msg: str) -> None:
    # Der Grund darf höchstens 123 Byte lang sein.
    await ws.close(code=code, reason=msg.encode("utf-8")[:120].decode("utf-8", "ignore"))


def target_allowed(host: str, cfg: dict[str, Any]) -> bool:
    """Nur Ziele in den eingerichteten Netzen – oder eigene Geräte."""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    for m in cfg.get("manual_devices") or []:
        if m.get("ip") == host:
            return True
    for net in cfg.get("subnets") or []:
        try:
            if ip in ipaddress.ip_network(net, strict=False):
                return True
        except ValueError:
            continue
    return False


def _friendly(exc: Exception) -> str:
    """Fehler so, dass man weiß was zu tun ist – nicht als Python-Spur."""
    if isinstance(exc, asyncssh.PermissionDenied):
        return "Anmeldung abgelehnt. Benutzername oder Passwort stimmt nicht."
    if isinstance(exc, asyncio.TimeoutError):
        return "Zeitüberschreitung. Antwortet das Gerät auf diesem Port?"
    if isinstance(exc, (ConnectionRefusedError, OSError)):
        return "Verbindung abgelehnt. Läuft dort ein SSH-Dienst?"
    if isinstance(exc, asyncssh.Error):
        return f"SSH-Fehler: {exc}"
    return "Verbindung fehlgeschlagen."


async def _pump_out(proc, ws: WebSocket) -> None:
    """Vom Gerät zum Browser."""
    try:
        while True:
            data = await proc.stdout.read(4096)
            if not data:
                break
            await ws.send_text(data)
    except (asyncssh.Error, WebSocketDisconnect, RuntimeError):
        pass


async def _pump_in(proc, ws: WebSocket) -> None:
    """Vom Browser zum Gerät."""
    try:
        while True:
            msg = await ws.receive_text()
            if not msg:
                continue
            kind, payload = msg[0], msg[1:]
            if kind == "d":               # Tastendruck
                proc.stdin.write(payload)
            elif kind == "r":             # Fenstergröße
                try:
                    size = json.loads(payload)
                    proc.change_terminal_size(int(size["cols"]), int(size["rows"]))
                except (json.JSONDecodeError, KeyError, ValueError, asyncssh.Error):
                    pass
    except (WebSocketDisconnect, RuntimeError, asyncssh.Error):
        pass


async def bridge(ws: WebSocket, cfg: dict[str, Any], on_user) -> None:
    """Eine Sitzung. Läuft, bis eine Seite auflegt."""
    await ws.accept()

    if not cfg.get("ssh_enabled"):
        return await _fail(ws, CLOSE_DENIED,
                           "SSH ist in den Einstellungen abgeschaltet.")

    try:
        init = json.loads(await ws.receive_text())
    except (json.JSONDecodeError, WebSocketDisconnect):
        return

    host = str(init.get("host", "")).strip()
    user = str(init.get("user", "")).strip()
    password = init.get("password") or ""
    try:
        port = int(init.get("port", 22))
    except (TypeError, ValueError):
        port = 22
    cols = int(init.get("cols") or 80)
    rows = int(init.get("rows") or 24)

    if not target_allowed(host, cfg):
        return await _fail(ws, CLOSE_DENIED,
                           f"{host} liegt in keinem deiner eingerichteten Netze.")

    conn = None
    try:
        conn = await asyncio.wait_for(
            asyncssh.connect(host, port=port, username=user, password=password,
                             # Im LAN gibt es keine gepflegte known_hosts-Datei.
                             known_hosts=None),
            timeout=CONNECT_TIMEOUT)
    except Exception as exc:
        return await _fail(ws, CLOSE_FAILED, _friendly(exc))

    # Erst jetzt merken: der Name hat sich als richtig erwiesen.
    try:
        on_user(host, user)
    except Exception:
        pass

    try:
        proc = await conn.create_process(term_type="xterm-256color",
                                         term_size=(cols, rows))
    except asyncssh.Error as exc:
        conn.close()
        return await _fail(ws, CLOSE_FAILED, _friendly(exc))

    await ws.send_text(READY)   # ab hier ist alles echte Ausgabe

    # Beide Richtungen laufen parallel. Wer zuerst endet – die Gegenseite legt
    # auf oder der Tab wird geschlossen – beendet die Sitzung.
    pump = asyncio.create_task(_pump_out(proc, ws))
    keys = asyncio.create_task(_pump_in(proc, ws))
    try:
        await asyncio.wait({pump, keys}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in (pump, keys):
            task.cancel()
        try:
            proc.close()
        except Exception:
            pass
        conn.close()
        try:
            await ws.close()
        except RuntimeError:
            pass
