"""Netboard aktualisiert sich selbst.

Ablauf, bewusst in kleinen, überprüfbaren Schritten:

1. Das Quell-Archiv der gewünschten Veröffentlichung von GitHub laden.
2. In einen Ordner neben den Daten entpacken (``/data/update/staging``).
3. **Prüfen**, ob darin wirklich ein Netboard steckt – erst dann weitermachen.
4. Die laufende Fassung nach ``/data/update/backup`` sichern.
5. Den Programmordner austauschen und den Vorgang beenden.

Schritt 5 klingt drastisch, ist aber genau richtig: Docker startet den Container
wegen ``restart: unless-stopped`` sofort neu – und dann läuft die neue Fassung.
Geht beim Austausch etwas schief, wird die Sicherung zurückgespielt, bevor
überhaupt neu gestartet wird.
"""
from __future__ import annotations

import asyncio
import io
import os
import shutil
import sys
import tarfile
import time
from pathlib import Path
from typing import Any

import httpx

from . import config as C
from . import release as R

APP_DIR = Path(__file__).resolve().parent          # …/app
WORK = C.DATA_DIR / "update"
STAGING = WORK / "staging"
BACKUP = WORK / "backup"
MAX_ARCHIVE = 60 * 1024 * 1024                     # 60 MB reichen weit

#: Fortschritt für die Oberfläche. Wird beim Lauf fortgeschrieben.
STATE: dict[str, Any] = {"running": False, "step": "", "pct": 0,
                         "ok": None, "error": "", "target": "",
                         "started": 0, "bytes": 0, "total": 0, "beat": 0}


def _set(step: str, pct: int, **extra: Any) -> None:
    """Fortschritt festhalten. ``beat`` ist der Zeitstempel der letzten
    Regung – daran erkennt die Oberfläche, ob noch etwas passiert."""
    STATE.update({"step": step, "pct": pct, "beat": time.time(), **extra})


async def latest() -> dict[str, Any]:
    """Jüngste Veröffentlichung von GitHub – oder eine erklärende Fehlermeldung."""
    url = f"https://api.github.com/repos/{R.REPO}/releases/latest"
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as c:
            r = await c.get(url, headers={"Accept": "application/vnd.github+json",
                                          "User-Agent": "Netboard"})
    except httpx.HTTPError as exc:
        return {"ok": False, "error": f"GitHub nicht erreichbar ({type(exc).__name__})."}
    if r.status_code == 404:
        return {"ok": True, "latest": None,
                "note": "Im Repository gibt es noch keine Veröffentlichung."}
    if r.status_code != 200:
        return {"ok": False, "error": f"GitHub antwortete mit {r.status_code}."}
    j = r.json()
    tag = str(j.get("tag_name") or j.get("name") or "")
    return {"ok": True, "latest": tag, "url": j.get("html_url") or "",
            "tarball": j.get("tarball_url") or "",
            "published": (j.get("published_at") or "")[:10],
            "body": (j.get("body") or "")[:4000],
            "name": (j.get("name") or "").strip()}


def _num(v: str) -> tuple[int, ...]:
    import re
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", str(v or ""))
    return tuple(int(x) for x in m.groups()) if m else (0, 0, 0)


def is_newer(remote: str, local: str = R.VERSION) -> bool:
    return _num(remote) > _num(local)


def _looks_like_netboard(root: Path) -> Path | None:
    """Den ``app``-Ordner im entpackten Archiv finden.

    GitHub packt immer einen Wurzelordner drumherum (``LucaFiveM-netboard-abc123``),
    und je nachdem, wie das Projekt im Repository liegt, steckt ``app`` noch eine
    Ebene tiefer (etwa ``netboard/app``). Darum wird gesucht statt geraten – in
    begrenzter Tiefe, damit das auch bei großen Archiven flott bleibt.
    """
    if not root.exists():
        return None

    def is_app(p: Path) -> bool:
        return (p / "main.py").is_file() and (p / "static" / "index.html").is_file()

    # Breitensuche: der am weitesten oben liegende Treffer gewinnt.
    queue: list[tuple[Path, int]] = [(root, 0)]
    seen = 0
    while queue and seen < 400:
        cur, depth = queue.pop(0)
        seen += 1
        if is_app(cur):
            return cur
        if depth >= 4:
            continue
        try:
            for child in sorted(cur.iterdir()):
                if child.is_dir() and child.name not in {
                        "__pycache__", ".git", "node_modules", "data"}:
                    queue.append((child, depth + 1))
        except OSError:
            continue
    return None


async def _download(url: str) -> bytes:
    """Archiv laden und dabei laufend melden, wie viel schon da ist."""
    async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as c:
        async with c.stream("GET", url, headers={"User-Agent": "Netboard"}) as r:
            if r.status_code != 200:
                raise RuntimeError(f"Download fehlgeschlagen ({r.status_code}).")
            total = int(r.headers.get("content-length") or 0)
            STATE["total"] = total
            buf = io.BytesIO()
            last = 0.0
            async for chunk in r.aiter_bytes():
                buf.write(chunk)
                got = buf.tell()
                if got > MAX_ARCHIVE:
                    raise RuntimeError("Archiv ist unerwartet groß – abgebrochen.")
                now = time.time()
                if now - last > 0.25:      # nicht öfter als viermal je Sekunde
                    last = now
                    # 10 % bis 45 % der Gesamtanzeige entfallen auf den Download.
                    share = (got / total) if total else 0.0
                    _set("Lade neue Fassung", 10 + int(35 * min(share, 1.0)),
                         bytes=got)
            STATE["bytes"] = buf.tell()
            return buf.getvalue()


def _safe_extract(data: bytes, dest: Path) -> None:
    """Archiv entpacken und dabei Ausbrüche aus dem Zielordner verhindern."""
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        for member in tar.getmembers():
            target = (dest / member.name).resolve()
            if not str(target).startswith(str(dest.resolve())):
                raise RuntimeError("Archiv enthält verdächtige Pfade.")
            if member.issym() or member.islnk():
                continue        # Verweise brauchen wir nicht – und sie sind riskant
        tar.extractall(dest)     # noqa: S202 – Pfade sind oben geprüft


def _swap(new_app: Path) -> None:
    """Programmordner austauschen – ohne Zwischenzustand.

    Erst wird der neue Stand vollständig **neben** dem alten aufgebaut
    (``app.new``). Erst wenn das fertig ist, treten zwei Umbenennungen in Kraft:
    ``app`` → ``app.old`` und ``app.new`` → ``app``. Umbenennen im selben
    Dateisystem ist unteilbar – es gibt also keinen Moment, in dem nur die Hälfte
    da wäre. Geht davor etwas schief, bleibt die laufende Fassung unberührt.
    """
    parent = APP_DIR.parent
    staged = parent / "app.new"
    old = parent / "app.old"
    shutil.rmtree(staged, ignore_errors=True)
    shutil.rmtree(old, ignore_errors=True)

    # 1) Neuen Stand komplett aufbauen. Fehler hier kosten nichts.
    shutil.copytree(new_app, staged)
    if not (staged / "main.py").is_file() or not (staged / "static" / "index.html").is_file():
        shutil.rmtree(staged, ignore_errors=True)
        raise RuntimeError("Der vorbereitete Stand ist unvollständig.")

    # 2) Sicherung der laufenden Fassung, damit man zurück kann.
    if BACKUP.exists():
        shutil.rmtree(BACKUP, ignore_errors=True)
    BACKUP.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copytree(APP_DIR, BACKUP,
                        ignore=shutil.ignore_patterns("__pycache__"))
    except OSError:
        pass        # Eine fehlende Sicherung darf das Update nicht verhindern

    # 3) Umschalten.
    os.rename(APP_DIR, old)
    try:
        os.rename(staged, APP_DIR)
    except OSError:
        os.rename(old, APP_DIR)      # zurück auf die laufende Fassung
        shutil.rmtree(staged, ignore_errors=True)
        raise
    shutil.rmtree(old, ignore_errors=True)


async def _install_requirements(src_root: Path) -> None:
    """Neue Abhängigkeiten nachziehen, falls sich die Liste geändert hat."""
    req = next((p for p in (src_root / "requirements.txt",
                            src_root.parent / "requirements.txt") if p.is_file()), None)
    if not req:
        return
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "pip", "install", "--no-cache-dir",
            "--disable-pip-version-check", "-r", str(req),
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        await asyncio.wait_for(proc.wait(), timeout=180)
    except (OSError, asyncio.TimeoutError):
        pass        # Fehlt etwas, meldet sich der Neustart – das Update selbst steht


async def run(tag: str, tarball: str) -> None:
    """Den kompletten Ablauf durchführen. Läuft im Hintergrund."""
    STATE.update({"running": True, "ok": None, "error": "", "target": tag,
                  "step": "Vorbereiten", "pct": 3, "started": time.time(),
                  "bytes": 0, "total": 0, "beat": time.time()})
    try:
        shutil.rmtree(STAGING, ignore_errors=True)
        _set("Lade neue Fassung", 10)
        url = tarball or f"https://api.github.com/repos/{R.REPO}/tarball/{tag}"
        data = await _download(url)
        _set("Archiv entpacken", 48)
        _safe_extract(data, STAGING)
        new_app = _looks_like_netboard(STAGING)
        if not new_app:
            # Beim Suchen helfen: sagen, was tatsächlich im Archiv lag.
            try:
                top = sorted(p.name for p in STAGING.iterdir())[:6]
                inner = []
                for d in STAGING.iterdir():
                    if d.is_dir():
                        inner = sorted(p.name for p in d.iterdir())[:8]
                        break
                hint = f" Gefunden wurde: {', '.join(inner or top) or 'nichts'}."
            except OSError:
                hint = ""
            raise RuntimeError(
                "Im Archiv fehlt der Ordner „app“ mit main.py und static/index.html."
                + hint + " Liegt das Projekt im Repository richtig?")
        _set("Abhängigkeiten prüfen", 62)
        await _install_requirements(new_app)
        _set("Dateien austauschen", 82)
        _swap(new_app)
        shutil.rmtree(STAGING, ignore_errors=True)
        (WORK / "last.txt").write_text(f"{tag} {int(time.time())}\n", "utf-8")
        _set("Neu starten", 97)
        STATE.update({"ok": True, "running": False, "step": "Neustart läuft", "pct": 100})
        # Kurz warten, damit die Oberfläche den Stand noch abholen kann.
        await asyncio.sleep(1.5)
        os._exit(0)          # Docker startet den Dienst neu – dann mit neuem Code
    except Exception as exc:
        STATE.update({"running": False, "ok": False, "pct": 0,
                      "step": "", "error": str(exc) or type(exc).__name__})
