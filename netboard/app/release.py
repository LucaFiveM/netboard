"""Version, Codename und Änderungsprotokoll von Netboard.

Jede Veröffentlichung bekommt eine Nummer und einen Namen – so wie macOS
„Sequoia“ heißt. Die Namen folgen einem Thema: **Himmelserscheinungen**, weil
Netboard einen Überblick verschafft. Kurz, gut aussprechbar, keine Wiederholung.

Beim Ausliefern wird hier oben ein neuer Eintrag ergänzt, ``VERSION`` und
``CODENAME`` angepasst – mehr ist nicht zu tun. Die Oberfläche liest den
Änderungstext direkt von hier, und der Updater vergleicht die Nummer mit dem
jüngsten Release auf GitHub.

Nummernschema (semantisch):
* **X.0.0** – große Umbauten, neue Grundfunktionen
* **1.X.0** – neue Funktionen, neuer Codename
* **1.1.X** – Fehlerbehebungen, Codename bleibt
"""
from __future__ import annotations

# Fest verdrahtet: von hier bezieht Netboard seine Aktualisierungen.
REPO = "lucafivem/netboard"

VERSION = "1.3.0"
CODENAME = "Zenith"

#: Jüngste Veröffentlichung zuerst. ``notes`` erscheint im Update-Fenster.
CHANGELOG: list[dict] = [
    {
        "version": "1.3.0",
        "name": "Zenith",
        "date": "2026-07-24",
        "notes": [
            "Update-Fenster zeigt jetzt Größe, Fortschritt und Laufzeit",
            "Selbst-Update findet das Programm in jeder Repository-Struktur",
            "Automatische Logos gibt es nur noch im Online-Betrieb – sichtbar "
            "ausgegraut, statt wirkungslos anwählbar",
            "Klare Meldung, wenn ein Update länger dauert als üblich",
        ],
    },
    {
        "version": "1.2.0",
        "name": "Solstice",
        "date": "2026-07-24",
        "notes": [
            "Offline-Betrieb: keinerlei Anfragen nach draußen, jederzeit umschaltbar",
            "QR-Code für die Zwei-Faktor-Anmeldung wird lokal erzeugt",
            "Update-Fenster mit Namen und Änderungen der neuen Fassung",
            "Einstellungen fangen Fehler ab, statt stehen zu bleiben",
            "Zwei-Faktor: klare Meldungen und Warnung bei falscher Serveruhr",
        ],
    },
    {
        "version": "1.1.0",
        "name": "Meridian",
        "date": "2026-07-23",
        "notes": [
            "Mehrere Benutzerkonten mit Verwalter- und Benutzerrolle",
            "Zwei-Faktor-Anmeldung mit Authenticator-Apps und Ersatzcodes",
            "Dashboards teilen – mit Hinweis für die andere Person",
            "Selbst-Update: neue Fassungen lassen sich direkt einspielen",
            "Eigene Schrift jetzt auch für Zahlen und Adressen",
            "Verlauf der Erreichbarkeit der letzten 24 Stunden",
        ],
    },
    {
        "version": "1.0.0",
        "name": "Horizon",
        "date": "2026-07-22",
        "notes": [
            "Erste Fassung: Netzwerk-Suchlauf, Dashboards, Kacheln, Ordner",
            "Live-Werte von Proxmox und ESXi, VM-Namen automatisch",
            "System- und Update-Prüfung über SSH, Wake-on-LAN",
        ],
    },
]

#: Namen, die für kommende Veröffentlichungen bereitliegen.
UPCOMING_NAMES = ("Aurora", "Halcyon", "Corona",
                  "Perihel", "Nimbus", "Alpenglow", "Equinox", "Lumen")


def entry(version: str) -> dict | None:
    """Den Protokolleintrag zu einer Version heraussuchen."""
    return next((c for c in CHANGELOG if c["version"] == version), None)


def current() -> dict:
    return entry(VERSION) or {"version": VERSION, "name": CODENAME,
                              "date": "", "notes": []}
