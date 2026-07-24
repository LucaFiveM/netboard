"""Betriebssystem und offene Updates eines Geräts per SSH auslesen.

Rein lesend: ``cat /etc/os-release`` für Name/Version und – je nach Distribution
– ein Trockenlauf des Paketmanagers für die Zahl offener Updates. Es wird nichts
installiert oder verändert. Die Zugangsdaten liegen verschlüsselt (siehe
``secretstore``) und werden nur für die Verbindung benutzt.
"""
from __future__ import annotations

import re
import shlex
import time
from typing import Any

import asyncssh


def parse_os_release(text: str) -> dict[str, str]:
    """/etc/os-release (KEY=VALUE, evtl. in Anführungszeichen) zu einem Dict."""
    out: dict[str, str] = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        out[key.strip()] = val.strip().strip('"').strip("'")
    return {
        "id": out.get("ID", ""),
        "name": out.get("NAME", ""),
        "version": out.get("VERSION_ID", ""),
        "pretty": out.get("PRETTY_NAME", "") or out.get("NAME", ""),
    }


def _update_command(os_id: str) -> str | None:
    """Passenden Trockenlauf je Distribution wählen. Nur lesend."""
    debianish = {"debian", "ubuntu", "raspbian", "linuxmint", "pop", "devuan"}
    rhelish = {"fedora", "rhel", "centos", "rocky", "almalinux"}
    suseish = {"opensuse", "opensuse-leap", "opensuse-tumbleweed", "sles"}
    if os_id in debianish:
        # Zeilen mit „Inst“ = zu aktualisierende Pakete.
        return "apt-get -s -o Debug::NoLocking=true upgrade 2>/dev/null | grep -c '^Inst' || true"
    if os_id in rhelish:
        return "dnf -q check-update 2>/dev/null | grep -Ec '^[a-zA-Z0-9]' || true"
    if os_id in suseish:
        return "zypper -q list-updates 2>/dev/null | grep -c '^v ' || true"
    if os_id == "alpine":
        return "apk version -l '<' 2>/dev/null | grep -c . || true"
    if os_id == "arch":
        return "checkupdates 2>/dev/null | grep -c . || true"
    return None


def parse_update_count(text: str) -> int | None:
    """Erste Zahl aus der Ausgabe – oder None, wenn nichts Brauchbares kam."""
    for token in (text or "").split():
        if token.isdigit():
            return int(token)
    return None


def _update_list_command(os_id: str) -> str | None:
    """Liste der aktualisierbaren Pakete (Name, alt/neu, Quelle). Nur lesend.
    ``LC_ALL=C`` erzwingt englische Ausgabe – sonst steht auf deutschen Systemen
    „aktualisierbar von“ da und nichts passt mehr."""
    debianish = {"debian", "ubuntu", "raspbian", "linuxmint", "pop", "devuan"}
    if os_id in debianish:
        return "LC_ALL=C apt list --upgradable 2>/dev/null || true"
    return None


# „paket/quelle 1.2 amd64 [upgradable from: 1.1]“ – die Klammer je nach Sprache
# unterschiedlich beschriftet, darum nur auf die Versionen achten.
_UPGRADABLE_RE = re.compile(
    r"^(?P<name>[^/\s]+)/(?P<repos>\S+)\s+(?P<new>\S+)\s+\S+\s+"
    r"\[[^\]:]*:\s*(?P<old>[^\]]+)\]")


def parse_upgradable(text: str) -> list[dict[str, Any]]:
    """apt-list-Ausgabe -> [{name, old, new, security}]. Sicherheits-Updates
    erkennt man an der Quelle (…-security)."""
    out: list[dict[str, Any]] = []
    for line in (text or "").splitlines():
        m = _UPGRADABLE_RE.match(line.strip())
        if not m:
            continue
        repos = m.group("repos").lower()
        out.append({
            "name": m.group("name"),
            "old": m.group("old").strip(),
            "new": m.group("new").strip(),
            "security": "security" in repos,
        })
    # Sicherheitsrelevante zuerst, dann alphabetisch.
    out.sort(key=lambda p: (not p["security"], p["name"]))
    return out


def _upgrade_command(os_id: str) -> str | None:
    """Vollständiges, nicht-interaktives Upgrade je Distribution. Braucht root."""
    debianish = {"debian", "ubuntu", "raspbian", "linuxmint", "pop", "devuan"}
    rhelish = {"fedora", "rhel", "centos", "rocky", "almalinux"}
    suseish = {"opensuse", "opensuse-leap", "opensuse-tumbleweed", "sles"}
    if os_id in debianish:
        return ("export DEBIAN_FRONTEND=noninteractive; apt-get update && "
                "apt-get -y -o Dpkg::Options::=--force-confold upgrade")
    if os_id in rhelish:
        return "dnf -y upgrade"
    if os_id in suseish:
        return "zypper -n update"
    if os_id == "alpine":
        return "apk update && apk upgrade"
    if os_id == "arch":
        return "pacman -Syu --noconfirm"
    return None


def with_privileges(cmd: str, user: str) -> str:
    """Als root direkt, sonst über ``sudo``. Das Passwort kommt über die
    Standardeingabe (``-S``), damit auch Konten ohne NOPASSWD funktionieren."""
    if (user or "").strip() == "root":
        return cmd
    return "sudo -S -p '' sh -c " + shlex.quote(cmd)


_SUDO_HINTS = (
    ("incorrect password", "sudo hat das Passwort abgelehnt."),
    ("is not in the sudoers", "Dieser Benutzer darf kein sudo verwenden."),
    ("a password is required", "sudo verlangt ein Passwort – bitte das SSH-Passwort hinterlegen."),
    ("permission denied", "Keine Rechte – der Benutzer darf keine Updates einspielen."),
    ("could not get lock", "Der Paketmanager ist gerade belegt (anderes Update läuft?)."),
    ("unable to acquire the dpkg frontend lock", "Der Paketmanager ist gerade belegt."),
)


def explain_failure(lines: list[str]) -> str | None:
    """Aus der Ausgabe eine verständliche Ursache ableiten (oder None)."""
    text = "\n".join(lines[-40:]).lower()
    for needle, msg in _SUDO_HINTS:
        if needle in text:
            return msg
    return None


async def run_update(host: str, user: str, password: str, os_id: str,
                     timeout: float = 8.0) -> dict[str, Any]:
    """Updates tatsächlich einspielen. Gibt ``{ok, log}`` bzw. ``{ok:False,error}``
    zurück. Fehler werfen nie."""
    cmd = _upgrade_command(os_id)
    if not cmd:
        return {"ok": False, "error": f"Für „{os_id or 'dieses System'}“ nicht unterstützt."}
    try:
        async with asyncssh.connect(host, username=user, password=password,
                                    known_hosts=None, connect_timeout=timeout) as conn:
            res = await conn.run(cmd, check=False, timeout=600)
            out = ((res.stdout or "") + (res.stderr or "")).strip()
            tail = "\n".join(out.splitlines()[-12:])   # nur das Ende zeigen
            ok = (res.exit_status == 0)
            return {"ok": ok, "log": tail or ("Fertig." if ok else "Ohne Ausgabe."),
                    "error": None if ok else "Das Upgrade meldete einen Fehler (evtl. keine root-Rechte)."}
    except asyncssh.PermissionDenied:
        return {"ok": False, "error": "Anmeldung abgelehnt (Benutzer/Passwort)."}
    except (asyncssh.Error, OSError, TimeoutError) as exc:
        return {"ok": False, "error": f"Nicht erreichbar: {type(exc).__name__}."}


async def run_update_stream(host: str, user: str, password: str, os_id: str,
                            timeout: float = 8.0):
    """Wie ``run_update``, aber als asynchroner Generator: liefert die Ausgabe
    Zeile für Zeile live mit, während das Upgrade läuft. Am Ende ein
    ``{"done": True, "ok": bool}``. Fehler werden als Zeilen gemeldet, nie
    geworfen."""
    cmd = _upgrade_command(os_id)
    if not cmd:
        yield {"line": f"Für „{os_id or 'dieses System'}“ nicht unterstützt."}
        yield {"done": True, "ok": False}
        return
    needs_sudo = (user or "").strip() != "root"
    full = with_privileges(cmd, user)
    try:
        async with asyncssh.connect(host, username=user, password=password,
                                    known_hosts=None, connect_timeout=timeout) as conn:
            # stderr auf stdout, damit alles in einer Reihenfolge ankommt.
            async with conn.create_process(full + " 2>&1") as proc:
                if needs_sudo:
                    # sudo -S liest das Passwort von der Standardeingabe.
                    proc.stdin.write(f"{password}\n")
                    proc.stdin.write_eof()
                seen: list[str] = []
                async for line in proc.stdout:
                    text = line.rstrip("\n")
                    if text:
                        seen.append(text)
                        yield {"line": text}
                await proc.wait_closed()
                ok = proc.exit_status == 0
                if not ok:
                    why = explain_failure(seen)
                    if why:
                        yield {"line": why}
                yield {"done": True, "ok": ok}
    except asyncssh.PermissionDenied:
        yield {"line": "Anmeldung abgelehnt (Benutzer/Passwort)."}
        yield {"done": True, "ok": False}
    except (asyncssh.Error, OSError, TimeoutError) as exc:
        yield {"line": f"Nicht erreichbar: {type(exc).__name__}."}
        yield {"done": True, "ok": False}


async def probe(host: str, user: str, password: str, timeout: float = 8.0) -> dict[str, Any]:
    """Ein Gerät prüfen. Gibt ``{ok, pretty, id, version, updates, checked}`` oder
    ``{ok: False, error}`` zurück – Fehler werfen nie."""
    try:
        async with asyncssh.connect(host, username=user, password=password,
                                    known_hosts=None, connect_timeout=timeout) as conn:
            osr = await conn.run("cat /etc/os-release", check=False)
            info = parse_os_release(osr.stdout or "")
            if not info.get("id"):
                # ESXi & Co. haben kein /etc/os-release. Sauber benennen statt
                # „fehlgeschlagen“ – Updates laufen dort über vCenter/esxcli.
                vm = await conn.run("vmware -v 2>/dev/null", check=False)
                vtext = (vm.stdout or "").strip()
                if vtext:
                    return {"ok": True, "pretty": vtext[:60], "id": "esxi",
                            "version": "", "updates": None, "packages": [],
                            "security": 0, "checked": int(time.time()),
                            "note": "Updates für ESXi laufen über vCenter/Update Manager "
                                    "oder esxcli – Netboard spielt sie nicht ein."}
                un = await conn.run("uname -sr 2>/dev/null", check=False)
                utext = (un.stdout or "").strip()
                if utext:
                    return {"ok": True, "pretty": utext[:60], "id": "",
                            "version": "", "updates": None, "packages": [],
                            "security": 0, "checked": int(time.time()),
                            "note": "Unbekanntes System – Updates werden hier nicht unterstützt."}
            updates: int | None = None
            packages: list[dict[str, Any]] = []
            cmd = _update_command(info.get("id", ""))
            if cmd:
                try:
                    res = await conn.run(cmd, check=False, timeout=timeout + 6)
                    updates = parse_update_count(res.stdout or "")
                except (asyncssh.Error, OSError, TimeoutError):
                    updates = None
            # Detailliste immer versuchen – sie ist genauer als die Zähl-Heuristik
            # und liefert zugleich Namen, Versionen und Sicherheitsrelevanz.
            list_cmd = _update_list_command(info.get("id", ""))
            if list_cmd:
                try:
                    lres = await conn.run(list_cmd, check=False, timeout=timeout + 6)
                    packages = parse_upgradable(lres.stdout or "")
                    if packages:
                        updates = len(packages)
                    elif updates is None:
                        updates = 0
                except (asyncssh.Error, OSError, TimeoutError):
                    packages = []
            return {"ok": True, "pretty": info["pretty"], "id": info["id"],
                    "version": info["version"], "updates": updates,
                    "packages": packages,
                    "security": sum(1 for p in packages if p.get("security")),
                    "checked": int(time.time())}
    except asyncssh.PermissionDenied:
        return {"ok": False, "error": "Anmeldung abgelehnt (Benutzer/Passwort)."}
    except (asyncssh.Error, OSError, TimeoutError) as exc:
        return {"ok": False, "error": f"Nicht erreichbar: {type(exc).__name__}."}
