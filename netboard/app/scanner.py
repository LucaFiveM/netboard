"""Netzwerk-Discovery via nmap. Findet erreichbare Hosts und offene Web-Ports."""
from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any

# Ports, die praktisch immer TLS sprechen -> Schema-Hinweis fürs Anreichern
HTTPS_HINT = {443, 8443, 9443, 8006, 8920, 10000}


@dataclass
class Service:
    port: int
    scheme: str = "http"          # http | https, wird beim Anreichern korrigiert
    title: str | None = None      # <title> der Seite
    ok: bool = False              # HTTP-Antwort erhalten?
    icon: bool = False            # Favicon vorhanden und zwischengespeichert?
    ms: int | None = None         # Antwortzeit in Millisekunden


@dataclass
class Device:
    ip: str
    hostname: str | None = None
    mac: str | None = None
    vendor: str | None = None
    is_gateway: bool = False
    services: list[Service] = field(default_factory=list)
    alias: str | None = None      # vom Nutzer vergebener Wunschname
    manual: bool = False          # selbst angelegt statt gefunden?
    manual_id: str | None = None  # Kennung des eigenen Eintrags
    ssh_port: int | None = None   # offener SSH-Port, falls gefunden

    @property
    def label(self) -> str:
        """Der Name, unter dem man das Gerät wiedererkennt.

        Der Hersteller allein reicht nicht: Drei Fritz-Geräte hießen sonst alle
        „AVM“ und wären in einer Kachelwand nicht auseinanderzuhalten. Das
        letzte Oktett macht sie eindeutig – und genau daran merkt man sich sein
        Netz ohnehin.
        """
        if self.alias:
            return self.alias
        if self.hostname:
            return self.hostname
        if self.vendor:
            return f"{self.vendor} .{self.ip.rsplit('.', 1)[-1]}"
        return self.ip


# --- Netz-Umgebung erkennen --------------------------------------------------
async def _run(*cmd: str) -> str:
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
        )
    except FileNotFoundError:
        return ""
    out, _ = await proc.communicate()
    return out.decode(errors="replace")


async def detect_subnets() -> list[str]:
    """Lokale IPv4-Netze vom Host. Basis für den Einrichtungsassistenten."""
    nets: list[str] = []
    out = await _run("ip", "-o", "-4", "addr", "show")
    for line in out.splitlines():
        m = re.search(r"\binet\s+(\d+\.\d+\.\d+\.\d+/\d+)", line)
        if not m:
            continue
        try:
            net = ipaddress.ip_network(m.group(1), strict=False)
        except ValueError:
            continue
        # Loopback raus, Riesennetze raus, Docker-interne Bridges raus
        if net.is_loopback or net.prefixlen < 20:
            continue
        parts = line.split(":", 2)
        iface = parts[1].strip() if len(parts) > 1 else ""
        if iface.startswith(("docker", "br-", "veth")):
            continue
        if str(net) not in nets:
            nets.append(str(net))
    return nets


async def detect_gateway() -> str | None:
    out = await _run("ip", "route", "show", "default")
    m = re.search(r"default via (\d+\.\d+\.\d+\.\d+)", out)
    return m.group(1) if m else None


# --- nmap-Scan ---------------------------------------------------------------
async def _nmap_hosts(subnet: str) -> dict[str, Device]:
    """Ping-Scan: welche Hosts leben? Liefert MAC/Hersteller im selben Segment."""
    xml = await _run("nmap", "-sn", "-n", "-T4", "-oX", "-", subnet)
    return _parse_hosts(xml)


async def _nmap_ports(ips: list[str], ports: str) -> dict[str, list[int]]:
    if not ips:
        return {}
    xml = await _run("nmap", "-Pn", "-n", "--open", "-T4",
                     "-p", ports, "-oX", "-", *ips)
    return _parse_ports(xml)


def _parse_hosts(xml: str) -> dict[str, Device]:
    devices: dict[str, Device] = {}
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return devices
    for host in root.findall("host"):
        status = host.find("status")
        if status is not None and status.get("state") != "up":
            continue
        ip = mac = vendor = None
        for addr in host.findall("address"):
            t = addr.get("addrtype")
            if t == "ipv4":
                ip = addr.get("addr")
            elif t == "mac":
                mac = addr.get("addr")
                vendor = clean_vendor(addr.get("vendor"))
        if not ip:
            continue
        hn = host.find("hostnames/hostname")
        devices[ip] = Device(
            ip=ip, mac=mac, vendor=vendor,
            hostname=clean_hostname(hn.get("name")) if hn is not None else None)
    return devices


def _parse_ports(xml: str) -> dict[str, list[int]]:
    result: dict[str, list[int]] = {}
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return result
    for host in root.findall("host"):
        ip = None
        for addr in host.findall("address"):
            if addr.get("addrtype") == "ipv4":
                ip = addr.get("addr")
        if not ip:
            continue
        ports = []
        for p in host.findall("ports/port"):
            state = p.find("state")
            if state is not None and state.get("state") == "open":
                ports.append(int(p.get("portid")))
        if ports:
            result[ip] = sorted(ports)
    return result


# Suffixe, die Heimrouter an jeden Namen hängen. „sv-docker01.fritz.box“ will
# niemand lesen – der Name ist „sv-docker01“.
_DOMAIN_SUFFIXES = (".fritz.box", ".local", ".lan", ".home", ".home.arpa",
                    ".localdomain", ".speedport.ip", ".internal")


def clean_hostname(name: str | None) -> str | None:
    """Macht aus „SV-DOCKER01.fritz.box.“ ein „sv-docker01“."""
    if not name:
        return None
    n = name.strip().rstrip(".")
    low = n.lower()
    for suf in _DOMAIN_SUFFIXES:
        if low.endswith(suf):
            n = n[:-len(suf)]
            break
    else:
        # Unbekannte Domain: nur kürzen, wenn wirklich Host + Domain + Endung
        # vorliegt (host.firma.de). „fritz.box“ ist dagegen der ganze Name
        # und bleibt, wie er ist.
        if n.count(".") >= 2 and not n.replace(".", "").isdigit():
            head = n.split(".")[0]
            if len(head) >= 2:
                n = head
    return n or None


# Der Herstellereintrag aus der MAC-Datenbank ist der volle Registereintrag:
# „AVM Audiovisuelles Marketing und Computersysteme GmbH“. Als Gerätename
# unbrauchbar – gemeint ist „AVM“.
_VENDOR_NOISE = {
    # Rechtsformen
    "gmbh", "mbh", "ag", "kg", "ohg", "inc", "inc.", "incorporated", "ltd",
    "ltd.", "limited", "llc", "l.l.c.", "plc", "corp", "corp.", "corporation",
    "corporate", "co", "co.", "company", "sa", "s.a.", "sas", "srl", "s.r.l.",
    "bv", "b.v.", "nv", "n.v.", "oy", "ab", "a/s", "aps", "pty", "pte",
    "spa", "s.p.a.", "gmbh.", "kgaa", "ug", "se",
    # Füllwörter
    "technologies", "technology", "technologie", "technologien", "tech",
    "electronics", "electronic", "elektronik", "international", "intl",
    "communications", "communication", "systems", "system", "systeme",
    "computersysteme", "computer", "computers", "solutions", "solution",
    "networks", "network", "networking", "devices", "device", "digital",
    "industrial", "industries", "industry", "group", "holding", "holdings",
    "enterprise", "enterprises", "semiconductor", "semiconductors",
    "precision", "manufacturing", "trading", "innovation", "innovations",
    "products", "product", "marketing", "und", "and", "&", "of", "the",
    "engineering", "labs", "laboratories", "research", "development",
}


def clean_vendor(name: str | None) -> str | None:
    """Macht aus dem Registereintrag den Namen, den man kennt."""
    if not name:
        return None
    # Alles hinter dem ersten Komma ist fast immer Rechtsform-Beiwerk
    head = name.split(",")[0].strip()
    tokens = [t for t in re.split(r"\s+", head) if t]
    if not tokens:
        return None

    # Abkürzungen tragen für sich: AVM, ASUS, SONY, TP-LINK
    first = tokens[0].strip(".,")
    if 2 <= len(first) <= 5 and first.isupper() and first.isalpha():
        return first

    keep: list[str] = []
    for tok in tokens:
        bare = tok.strip(".,;:").lower()
        if bare in _VENDOR_NOISE or not bare:
            continue
        keep.append(tok.strip(".,;:"))
        if len(keep) == 2:      # zwei Wörter reichen für jeden Markennamen
            break
    out = " ".join(keep) or tokens[0].strip(".,")
    return out[:22] or None


def _reverse_dns(ip: str, subnets: list[str] | None = None) -> str | None:
    try:
        name = clean_hostname(socket.gethostbyaddr(ip)[0])
        if name:
            return name
    except (OSError, socket.herror):
        pass
    # Der öffentliche Resolver (im Container) kennt interne Namen wie
    # „sv-file01“ oft nicht -> den lokalen Router direkt per PTR fragen.
    try:
        from . import netdns
        return clean_hostname(netdns.reverse(ip, subnets))
    except Exception:
        return None


def expand_ports(spec: str) -> list[int]:
    """„80,443,8000-8002“ -> [80, 443, 8000, 8001, 8002]"""
    out: list[int] = []
    for part in re.split(r"[,\s]+", str(spec)):
        if not part:
            continue
        if "-" in part:
            a, _, b = part.partition("-")
            try:
                lo, hi = int(a), int(b)
            except ValueError:
                continue
            # Ein aufgeklappter Bereich darf die Liste nicht sprengen
            for p in range(lo, min(hi, lo + 255) + 1):
                if 1 <= p <= 65535 and p not in out:
                    out.append(p)
        else:
            try:
                p = int(part)
            except ValueError:
                continue
            if 1 <= p <= 65535 and p not in out:
                out.append(p)
    return out


def merge_manual(devices: dict[str, Device], manual: list[dict]) -> None:
    """Selbst angelegte Geräte einweben. Bestehende IPs werden ergänzt,
    unbekannte neu aufgenommen – auch wenn sie auf kein Ping antworten."""
    for entry in manual:
        ip = entry.get("ip")
        if not ip:
            continue
        dev = devices.get(ip)
        if dev is None:
            dev = Device(ip=ip)
            devices[ip] = dev
        dev.manual = True
        dev.manual_id = entry.get("id")
        if entry.get("name"):
            dev.alias = entry["name"]
        have = {s.port for s in dev.services}
        for port in expand_ports(entry.get("ports", "")):
            if port not in have:
                dev.services.append(Service(
                    port=port,
                    scheme="https" if port in HTTPS_HINT else "http"))


def sort_services(services: list[Service], priority: list[int]) -> list[Service]:
    """Bevorzugte Ports zuerst (in Nutzer-Reihenfolge), dann erreichbare, dann Port."""
    def key(s: Service):
        rank = priority.index(s.port) if s.port in priority else len(priority)
        return (rank, not s.ok, s.port)
    return sorted(services, key=key)


def _ip_key(ip: str):
    try:
        return tuple(int(o) for o in ip.split("."))
    except ValueError:
        return (999, 999, 999, 999)


def local_ips() -> set[str]:
    """Alle IPs dieses Hosts (für „das bin ich selbst“-Erkennung)."""
    import socket
    ips: set[str] = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None):
            ips.add(info[4][0])
    except OSError:
        pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 1))       # sendet nichts, wählt nur die Route
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    return {ip for ip in ips if ip and not ip.startswith("127.")}


def _host_lan_ip(subnets: list[str]) -> str | None:
    """Die eigene IP, die in einem der gescannten Subnetze liegt (z. B. .18)."""
    import ipaddress
    mine = local_ips()
    for sn in subnets:
        try:
            net = ipaddress.ip_network(sn, strict=False)
        except ValueError:
            continue
        for ip in mine:
            try:
                if ipaddress.ip_address(ip) in net:
                    return ip
            except ValueError:
                continue
    return next(iter(mine), None)


async def discover(cfg: dict[str, Any]) -> tuple[list[Device], list[Device], dict]:
    """Vollständiger Discovery-Lauf.

    Gibt (sichtbare Geräte, deaktivierte Geräte, Metadaten) zurück.
    Deaktivierte werden früh aussortiert – sie werden gar nicht erst abgefragt.
    """
    subnets = cfg.get("subnets") or await detect_subnets()
    gateway = await detect_gateway()
    aliases = cfg.get("aliases") or {}
    priority = cfg.get("priority_ports") or []
    hidden_ips = set(cfg.get("hidden") or [])
    manual = cfg.get("manual_devices") or []
    # SSH-Ports werden mitgescannt, aber nicht als Web-Dienst geführt.
    ssh_ports = (set(expand_ports(cfg.get("ssh_ports", "22")))
                 if cfg.get("ssh_enabled") else set())

    # 1) lebende Hosts über alle Subnetze
    found: dict[str, Device] = {}
    for host_map in await asyncio.gather(*(_nmap_hosts(s) for s in subnets)):
        for ip, dev in host_map.items():
            found.setdefault(ip, dev)

    # 2) Deaktivierte raustrennen. Ein eigenes Gerät gewinnt immer:
    #    wer es bewusst anlegt, will es auch sehen.
    manual_ips = {m["ip"] for m in manual if m.get("ip")}
    hidden: dict[str, Device] = {
        ip: found.pop(ip) for ip in list(found)
        if ip in hidden_ips and ip not in manual_ips
    }

    # 3) eigene Geräte einweben – auch solche, die auf kein Ping antworten
    merge_manual(found, manual)

    # 4) Portscan über alles Sichtbare – auch über eigene Geräte: die haben ggf.
    #    weitere offene Ports, und Deaktivierte sind hier bereits raus.
    spec = cfg["ports"] + ("," + cfg.get("ssh_ports", "22") if ssh_ports else "")
    port_map = await _nmap_ports(list(found), spec)

    # Eigener Host: Läuft Netboard im selben Docker wie andere Dienste (wg-easy,
    # paperless …), sind deren veröffentlichte Ports über die LAN-IP wegen
    # fehlendem Docker-Hairpin-NAT oft unsichtbar – über 127.0.0.1 aber schon.
    # Darum localhost mitscannen und dem eigenen Host zuordnen.
    host_ip = _host_lan_ip(subnets)
    if host_ip:
        try:
            local_ports = (await _nmap_ports(["127.0.0.1"], spec)).get("127.0.0.1", [])
        except (OSError, ValueError):
            local_ports = []
        if local_ports:
            if host_ip not in found and host_ip not in hidden_ips:
                found[host_ip] = Device(ip=host_ip)
            if host_ip in found:
                port_map[host_ip] = sorted(set(port_map.get(host_ip, [])) | set(local_ports))

    loop = asyncio.get_running_loop()
    resolve_names = cfg.get("resolve_names", True)
    devices: list[Device] = []
    for ip, dev in found.items():
        if resolve_names and not dev.hostname:  # nmap -n macht kein DNS -> selbst nachholen
            dev.hostname = await loop.run_in_executor(None, _reverse_dns, ip, subnets)
        if not dev.alias:
            dev.alias = aliases.get(ip)
        dev.is_gateway = bool(gateway and ip == gateway)
        have = {s.port for s in dev.services}
        for p in port_map.get(ip, []):
            if p in ssh_ports:
                if dev.ssh_port is None:
                    dev.ssh_port = p   # kein Web-Dienst -> eigener Weg
                continue
            if p not in have:
                dev.services.append(Service(
                    port=p, scheme="https" if p in HTTPS_HINT else "http"))
        dev.services = sort_services(dev.services, priority)
        devices.append(dev)

    # Gateway zuerst, dann Geräte mit Web-Diensten, dann Rest – je nach IP
    devices.sort(key=lambda d: (not d.is_gateway, not (d.services or d.ssh_port),
                                _ip_key(d.ip)))

    hidden_list = list(hidden.values())
    for d in hidden_list:
        d.alias = aliases.get(d.ip)
    hidden_list.sort(key=lambda d: _ip_key(d.ip))

    return devices, hidden_list, {"subnets": subnets, "gateway": gateway}
