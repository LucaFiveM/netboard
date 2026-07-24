"""Wake-on-LAN: ein „Magic Packet" an ein Gerät schicken, um es zu wecken.

Das Paket besteht aus 6× 0xFF gefolgt von 16 Wiederholungen der Ziel-MAC. Es
wird als UDP-Broadcast verschickt – an die allgemeine Broadcast-Adresse und die
des jeweiligen Subnetzes, damit es das Gerät auch in geswitchten Netzen erreicht.

Kein externes Paket nötig; nur die Standardbibliothek. Netboard kennt die
MAC-Adressen bereits aus dem Netzwerk-Scan.
"""
from __future__ import annotations

import re
import socket


def normalize_mac(mac: str) -> str | None:
    """MAC in 12 Hex-Zeichen wandeln (Trenner egal) – oder None bei Unfug."""
    cleaned = re.sub(r"[^0-9a-fA-F]", "", mac or "")
    return cleaned.lower() if len(cleaned) == 12 else None


def subnet_broadcast(ip: str) -> str | None:
    """Aus einer IPv4 die /24-Broadcast-Adresse ableiten (192.168.1.7 → .255)."""
    parts = (ip or "").split(".")
    if len(parts) == 4 and all(p.isdigit() and 0 <= int(p) <= 255 for p in parts):
        return ".".join(parts[:3] + ["255"])
    return None


def send_magic_packet(mac: str, broadcasts: list[str], ports=(9, 7)) -> int:
    """Magic Packet an alle Broadcast-Adressen/Ports schicken. Gibt die Zahl der
    erfolgreichen Sendungen zurück. Wirft ValueError bei ungültiger MAC."""
    hex_mac = normalize_mac(mac)
    if not hex_mac:
        raise ValueError("ungültige MAC-Adresse")
    packet = bytes.fromhex("ff" * 6 + hex_mac * 16)
    sent = 0
    for bc in broadcasts:
        for port in ports:
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                    s.sendto(packet, (bc, port))
                sent += 1
            except OSError:
                pass
    return sent
