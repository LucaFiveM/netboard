"""Verlauf der Erreichbarkeit: Wer war in den letzten 24 Stunden erreichbar?

Bei jedem Suchlauf wird je Gerät ein Messpunkt festgehalten (erreichbar ja/nein).
Die Punkte liegen in ``data/uptime.json`` und überleben so einen Neustart.
Alles, was älter als das Zeitfenster ist, fliegt beim Schreiben raus – die Datei
bleibt damit von selbst klein.

Bewusst schlicht: keine Datenbank, kein Zeitreihen-Dienst. Für ein Heimnetz mit
ein paar Dutzend Geräten und einem Messpunkt alle paar Minuten reicht das
locker, und man kann die Datei zur Not im Editor öffnen.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any

from . import config as C

WINDOW = 24 * 3600      # Zeitfenster: 24 Stunden
MAX_PER_IP = 600        # Obergrenze je Gerät (schützt vor Ausreißern)

_FILE = C.DATA_DIR / "uptime.json"
_data: dict[str, list[list]] | None = None      # {ip: [[zeit, 1|0], ...]}


def _load() -> dict[str, list[list]]:
    global _data
    if _data is None:
        try:
            raw = json.loads(_FILE.read_text("utf-8"))
            _data = {str(k): [[float(t), 1 if u else 0] for t, u in v]
                     for k, v in raw.items() if isinstance(v, list)}
        except (OSError, ValueError, TypeError):
            _data = {}
    return _data


def _save() -> None:
    if _data is None:
        return
    try:
        C.DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = _FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(_data, separators=(",", ":")), "utf-8")
        os.replace(tmp, _FILE)
    except OSError:
        pass        # Ein fehlgeschlagener Verlauf darf den Dienst nie stören


def record(samples: dict[str, bool], now: float | None = None) -> None:
    """Einen Messpunkt je Gerät festhalten und Altes wegräumen."""
    data = _load()
    now = now or time.time()
    cutoff = now - WINDOW
    for ip, up in samples.items():
        rows = data.setdefault(ip, [])
        rows.append([now, 1 if up else 0])
        # Zu alte Punkte und Überlänge kappen
        if rows and rows[0][0] < cutoff:
            data[ip] = [r for r in rows if r[0] >= cutoff]
        if len(data[ip]) > MAX_PER_IP:
            data[ip] = data[ip][-MAX_PER_IP:]
    # Geräte, von denen im Fenster nichts mehr übrig ist, ganz entfernen
    for ip in [k for k, v in data.items() if not v or v[-1][0] < cutoff]:
        data.pop(ip, None)
    _save()


def history(ip: str, buckets: int = 24, now: float | None = None) -> dict[str, Any]:
    """Verlauf eines Geräts als gleich große Abschnitte (Standard: 24×1 Stunde).

    Jeder Abschnitt bekommt ``pct`` (Anteil erreichbar) oder ``None``, wenn in
    dieser Stunde nichts gemessen wurde – „keine Daten“ ist etwas anderes als
    „war offline“ und wird auch anders dargestellt.
    """
    data = _load()
    rows = data.get(ip) or []
    now = now or time.time()
    start = now - WINDOW
    size = WINDOW / buckets
    out: list[dict[str, Any]] = []
    for i in range(buckets):
        b0 = start + i * size
        b1 = b0 + size
        hits = [r for r in rows if b0 <= r[0] < b1]
        if hits:
            up = sum(1 for r in hits if r[1])
            out.append({"pct": round(up * 100 / len(hits)), "n": len(hits),
                        "from": int(b0), "to": int(b1)})
        else:
            out.append({"pct": None, "n": 0, "from": int(b0), "to": int(b1)})
    total = [r for r in rows if r[0] >= start]
    overall = round(sum(1 for r in total if r[1]) * 100 / len(total)) if total else None
    return {"ip": ip, "buckets": out, "overall": overall,
            "samples": len(total), "window_h": WINDOW // 3600}


def forget(ip: str) -> None:
    """Verlauf eines Geräts löschen (z. B. wenn es dauerhaft verschwindet)."""
    data = _load()
    if data.pop(ip, None) is not None:
        _save()
