"""Alles rund um Geheimnisse an einem Ort.

Drei getrennte Aufgaben, drei richtige Werkzeuge:

* **Login-Passwort** → scrypt-Hash. Einweg, nicht wiederherstellbar. Selbst wer
  die Datei hat, bekommt das Passwort nicht zurück.
* **Dienst-Zugänge** (vSphere-Passwort, Proxmox-Token) → müssen benutzbar
  bleiben, also **verschlüsselt** (Fernet = AES-128-CBC + HMAC) mit einem
  Schlüssel, der nur für den Eigentümer lesbar neben den Daten liegt. Auf der
  Platte steht damit nie Klartext; an den Browser gehen sie ohnehin nie.
* **Sitzungen** → signierte Token (HMAC), damit ein Cookie nicht gefälscht
  werden kann.

Der Schlüssel wird beim ersten Bedarf erzeugt und mit ``chmod 600`` abgelegt.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from .config import DATA_DIR

KEY_FILE = DATA_DIR / "secret.key"
ENC_PREFIX = "enc:"

_fernet: Fernet | None = None
_sig_key: bytes | None = None


def _load_key() -> bytes:
    """Schlüsseldatei lesen oder einmalig erzeugen (nur für Eigentümer lesbar)."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if KEY_FILE.exists():
        return KEY_FILE.read_bytes().strip()
    key = Fernet.generate_key()
    KEY_FILE.write_bytes(key)
    try:
        os.chmod(KEY_FILE, 0o600)
    except OSError:
        pass
    return key


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        _fernet = Fernet(_load_key())
    return _fernet


def _get_sig_key() -> bytes:
    """Getrennter Schlüssel fürs Signieren von Sitzungen – abgeleitet aus dem
    Hauptschlüssel, aber nicht identisch."""
    global _sig_key
    if _sig_key is None:
        _sig_key = hashlib.sha256(b"netboard-session|" + _load_key()).digest()
    return _sig_key


# --- Verschlüsselung ruhender Geheimnisse ------------------------------------
def encrypt(value: str) -> str:
    """Klartext → ``enc:…``. Leeres bleibt leer, bereits Verschlüsseltes bleibt."""
    if not value or is_encrypted(value):
        return value or ""
    token = _get_fernet().encrypt(value.encode("utf-8")).decode("ascii")
    return ENC_PREFIX + token


def decrypt(value: str) -> str:
    """``enc:…`` → Klartext. Alles andere (auch alter Klartext) unverändert."""
    if not isinstance(value, str) or not value.startswith(ENC_PREFIX):
        return value or ""
    try:
        return _get_fernet().decrypt(value[len(ENC_PREFIX):].encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        return ""


def is_encrypted(value: str) -> bool:
    return isinstance(value, str) and value.startswith(ENC_PREFIX)


# --- Login-Passwort: scrypt-Hash ---------------------------------------------
def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=16384, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, salt_hex, hash_hex = stored.split("$", 2)
        if algo != "scrypt":
            return False
        salt = bytes.fromhex(salt_hex)
        dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=16384, r=8, p=1, dklen=32)
        return hmac.compare_digest(dk.hex(), hash_hex)
    except (ValueError, AttributeError):
        return False


# --- Sitzungen: signierte Token ----------------------------------------------
def make_session(username: str, days: int = 30) -> str:
    payload = {"u": username, "exp": int(time.time()) + days * 86400}
    raw = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=")
    sig = hmac.new(_get_sig_key(), raw, hashlib.sha256).digest()
    return raw.decode() + "." + base64.urlsafe_b64encode(sig).rstrip(b"=").decode()


def check_session(token: str) -> str | None:
    """Gibt den Benutzernamen zurück, wenn das Token gültig und frisch ist."""
    try:
        raw_s, sig_s = token.split(".", 1)
        raw = raw_s.encode()
        expected = hmac.new(_get_sig_key(), raw, hashlib.sha256).digest()
        got = base64.urlsafe_b64decode(sig_s + "=" * (-len(sig_s) % 4))
        if not hmac.compare_digest(expected, got):
            return None
        payload = json.loads(base64.urlsafe_b64decode(raw_s + "=" * (-len(raw_s) % 4)))
        if payload.get("exp", 0) < time.time():
            return None
        return payload.get("u")
    except (ValueError, KeyError, json.JSONDecodeError):
        return None
