"""Winziger DNS-A-Resolver als Fallback – ohne Fremdpaket, nur stdlib.

Warum das nötig ist: Läuft Netboard im Container, ersetzt Docker einen
Loopback-Resolver des Hosts (systemd-resolved lauscht auf ``127.0.0.53``)
regelmäßig durch **öffentliches** DNS. Öffentliche Server kennen aber keine
lokalen Namen wie ``vcenter01.fritz.box`` – die Verbindung per Name scheitert,
per IP klappt sie. Genau dieses „nur die IP geht“ ist das Symptom.

Wenn der normale Weg (``getaddrinfo``) versagt, fragen wir die Heimrouter
direkt: das Gateway eines Netzes (üblich ``.1``, z. B. die Fritzbox) beantwortet
lokale Namen selbst. Ein minimales A-Query über UDP genügt.
"""
from __future__ import annotations

import ipaddress
import re
import secrets
import socket
import struct
from pathlib import Path


def is_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def _build_query(name: str, qid: int) -> bytes:
    # Header: ID, Flags (RD=1), QDCOUNT=1, Rest 0.
    header = struct.pack(">HHHHHH", qid, 0x0100, 1, 0, 0, 0)
    body = b""
    for label in name.rstrip(".").split("."):
        raw = label.encode("ascii", "ignore")[:63]
        body += bytes([len(raw)]) + raw
    body += b"\x00" + struct.pack(">HH", 1, 1)   # QTYPE=A, QCLASS=IN
    return header + body


def _skip_name(data: bytes, i: int) -> int:
    while i < len(data):
        length = data[i]
        if length == 0:
            return i + 1
        if length & 0xC0 == 0xC0:          # Kompressions-Zeiger: 2 Bytes
            return i + 2
        i += 1 + length
    return i


def _parse_answer(data: bytes, qid: int) -> list[str]:
    if len(data) < 12:
        return []
    rid, _flags, qd, an, _ns, _ar = struct.unpack(">HHHHHH", data[:12])
    if rid != qid or an == 0:
        return []
    i = 12
    for _ in range(qd):                    # Fragenteil überspringen
        i = _skip_name(data, i) + 4
    out: list[str] = []
    for _ in range(an):
        i = _skip_name(data, i)
        if i + 10 > len(data):
            break
        rtype, _rclass, _ttl, rdlen = struct.unpack(">HHIH", data[i:i + 10])
        i += 10
        rdata = data[i:i + rdlen]
        i += rdlen
        if rtype == 1 and rdlen == 4:      # A-Record
            out.append(socket.inet_ntoa(rdata))
    return out


def query(name: str, resolvers: list[str], timeout: float = 1.5) -> str | None:
    """Erste IPv4 für ``name`` von einem der ``resolvers`` (Reihenfolge = Vorrang)."""
    for server in resolvers:
        if not server:
            continue
        qid = secrets.randbits(16)
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(timeout)
            sock.sendto(_build_query(name, qid), (server, 53))
            data, _ = sock.recvfrom(512)
            ips = _parse_answer(data, qid)
            if ips:
                return ips[0]
        except OSError:
            continue
        finally:
            if sock is not None:
                sock.close()
    return None


def _resolv_conf_servers() -> list[str]:
    """Nameserver aus /etc/resolv.conf – aber Loopback lassen wir weg, das ist
    ja genau der Weg, der im Container nicht funktioniert."""
    out: list[str] = []
    try:
        text = Path("/etc/resolv.conf").read_text("utf-8")
    except OSError:
        return out
    for m in re.finditer(r"^\s*nameserver\s+(\S+)", text, re.MULTILINE):
        ip = m.group(1)
        if is_ip(ip) and not ipaddress.ip_address(ip).is_loopback:
            out.append(ip)
    return out


def gateways_from_subnets(subnets: list[str]) -> list[str]:
    """Wahrscheinliche Router je Netz: die erste nutzbare Adresse (``.1``)."""
    out: list[str] = []
    for raw in subnets or []:
        try:
            net = ipaddress.ip_network(raw, strict=False)
        except ValueError:
            continue
        gw = str(net.network_address + 1)
        if gw not in out:
            out.append(gw)
    return out


def _read_name(data: bytes, i: int) -> tuple[str, int]:
    """Einen (evtl. komprimierten) DNS-Namen ab ``i`` lesen."""
    labels: list[str] = []
    jumped = False
    nxt = i
    steps = 0
    while i < len(data) and steps < 128:
        steps += 1
        length = data[i]
        if length == 0:
            if not jumped:
                nxt = i + 1
            break
        if length & 0xC0 == 0xC0:              # Kompressions-Zeiger
            if i + 1 >= len(data):
                break
            ptr = ((length & 0x3F) << 8) | data[i + 1]
            if not jumped:
                nxt = i + 2
            i = ptr
            jumped = True
            continue
        labels.append(data[i + 1:i + 1 + length].decode("ascii", "ignore"))
        i += 1 + length
    return ".".join(labels), nxt


def _build_ptr_query(ip: str, qid: int) -> bytes:
    header = struct.pack(">HHHHHH", qid, 0x0100, 1, 0, 0, 0)
    rev = ".".join(reversed(ip.split("."))) + ".in-addr.arpa"
    body = b""
    for label in rev.split("."):
        raw = label.encode("ascii", "ignore")[:63]
        body += bytes([len(raw)]) + raw
    body += b"\x00" + struct.pack(">HH", 12, 1)   # QTYPE=PTR, QCLASS=IN
    return header + body


def _parse_ptr(data: bytes, qid: int) -> str | None:
    if len(data) < 12:
        return None
    rid, _flags, qd, an, _ns, _ar = struct.unpack(">HHHHHH", data[:12])
    if rid != qid or an == 0:
        return None
    i = 12
    for _ in range(qd):
        i = _skip_name(data, i) + 4
    for _ in range(an):
        i = _skip_name(data, i)
        if i + 10 > len(data):
            break
        rtype, _rclass, _ttl, rdlen = struct.unpack(">HHIH", data[i:i + 10])
        i += 10
        if rtype == 12:                        # PTR-Record
            name, _ = _read_name(data, i)
            return name.rstrip(".") or None
        i += rdlen
    return None


def reverse(ip: str, subnets: list[str] | None = None, timeout: float = 1.2) -> str | None:
    """Interner Name zu einer IP (PTR) – erst resolv.conf, dann die Router.
    Öffentliche Resolver kennen lokale Namen wie ``sv-file01`` nicht; die
    Heimrouter beantworten sie meist selbst."""
    if not is_ip(ip):
        return None
    resolvers = _resolv_conf_servers() + gateways_from_subnets(subnets or [])
    for server in dict.fromkeys(resolvers):
        if not server:
            continue
        qid = secrets.randbits(16)
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(timeout)
            sock.sendto(_build_ptr_query(ip, qid), (server, 53))
            data, _ = sock.recvfrom(512)
            name = _parse_ptr(data, qid)
            if name:
                return name
        except OSError:
            continue
        finally:
            if sock is not None:
                sock.close()
    return None


def resolve(name: str, subnets: list[str] | None = None) -> str | None:
    """Vollständige Auflösung mit Fallback.

    1. Normaler Systemweg (``getaddrinfo``). Klappt der, sind wir fertig.
    2. Sonst der Reihe nach: echte Nameserver aus resolv.conf, dann die
       Gateways der eingerichteten Netze (Fritzbox & Co.).
    """
    if not name or is_ip(name):
        return name or None
    try:
        return socket.getaddrinfo(name, None, socket.AF_INET)[0][4][0]
    except OSError:
        pass
    resolvers = _resolv_conf_servers() + gateways_from_subnets(subnets or [])
    # Doppelte raus, Reihenfolge erhalten.
    seen: set[str] = set()
    ordered = [r for r in resolvers if not (r in seen or seen.add(r))]
    return query(name, ordered)
