"""Zwei-Faktor-Anmeldung mit Einmalcodes (TOTP, RFC 6238).

Das ist genau das Verfahren, das Google Authenticator, Aegis, 1Password & Co.
sprechen: aus einem geteilten Geheimnis und der aktuellen Uhrzeit entsteht alle
30 Sekunden ein sechsstelliger Code.

Nur Standardbibliothek – kein zusätzliches Paket nötig. Das Geheimnis liegt
verschlüsselt in der Konfiguration (siehe ``secretstore``).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import struct
import time
from urllib.parse import quote

STEP = 30           # Sekunden je Code
DIGITS = 6
WINDOW = 1          # ±1 Schritt Toleranz (Uhren laufen selten exakt gleich)


def new_secret() -> str:
    """Frisches Geheimnis als Base32 (das, was in der App landet)."""
    return base64.b32encode(os.urandom(20)).decode("ascii").rstrip("=")


def _code_at(secret: str, counter: int) -> str:
    key = base64.b32decode(secret.upper() + "=" * (-len(secret) % 8), casefold=True)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % (10 ** DIGITS)).zfill(DIGITS)


def code(secret: str, at: float | None = None) -> str:
    """Der Code, der gerade gilt."""
    return _code_at(secret, int((at or time.time()) // STEP))


def verify(secret: str, given: str, at: float | None = None) -> bool:
    """Code prüfen – mit kleiner Toleranz nach vorn und hinten."""
    given = "".join(ch for ch in str(given or "") if ch.isdigit())
    if len(given) != DIGITS or not secret:
        return False
    now = int((at or time.time()) // STEP)
    for drift in range(-WINDOW, WINDOW + 1):
        try:
            if hmac.compare_digest(_code_at(secret, now + drift), given):
                return True
        except (ValueError, TypeError):
            return False
    return False


def provisioning_uri(secret: str, account: str, issuer: str = "Netboard") -> str:
    """otpauth://-Adresse für den QR-Code der Authenticator-App."""
    label = quote(f"{issuer}:{account}", safe="")
    return (f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer)}"
            f"&algorithm=SHA1&digits={DIGITS}&period={STEP}")


def new_recovery_codes(count: int = 8) -> list[str]:
    """Ersatzcodes für den Fall, dass das Telefon weg ist."""
    return ["-".join(secrets.token_hex(2) for _ in range(2)) for _ in range(count)]
