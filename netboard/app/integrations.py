"""Live-Werte von Virtualisierungs-Hosts für einzelne Kacheln.

Proxmox VE liefert über seine REST-API je Node CPU-, RAM- und Laufzeitwerte.
Wir fragen das mit einem **API-Token** (nur Lesen genügt) ab und ordnen die
Werte der IP des Nodes zu – so zeigt die Kachel, die auf den Node zeigt (Port
8006), dessen Auslastung. Über ``/cluster/status`` bekommen wir Name → IP je
Node direkt, ohne raten zu müssen.

Der ESXi-/vCenter-Weg liegt in ``sysinfo`` (nutzt pyVmomi) und wird von
``read_all`` mit eingesammelt.
"""
from __future__ import annotations

import re
from typing import Any

import httpx

from .config import clean_hostport


def _pve_base(host: str) -> str:
    """Aus einer Nutzereingabe die API-Basis machen: ``https://host:8006``."""
    h, port = clean_hostport(host)
    if not port or port == 80 or port == 443:
        port = 8006
    return f"https://{h}:{port}"


async def _get(client: httpx.AsyncClient, base: str, path: str, headers: dict) -> Any:
    r = await client.get(base + path, headers=headers, timeout=8.0)
    r.raise_for_status()
    return (r.json() or {}).get("data")


def _ipv4s(interfaces: Any) -> list[str]:
    """Aus der Gastagent-Antwort die nutzbaren IPv4 herausziehen (kein Loopback)."""
    out: list[str] = []
    for nic in (interfaces or {}).get("result", []) if isinstance(interfaces, dict) else []:
        for a in nic.get("ip-addresses", []) or []:
            ip = a.get("ip-address", "")
            if a.get("ip-address-type") == "ipv4" and ip and not ip.startswith("127."):
                out.append(ip)
    return out


def _lxc_ip(cfg: Any) -> list[str]:
    """LXC-Container: statische IP aus net0 (ip=…/nn) ziehen, DHCP überspringen."""
    out: list[str] = []
    if not isinstance(cfg, dict):
        return out
    for key, val in cfg.items():
        if not key.startswith("net") or not isinstance(val, str):
            continue
        m = re.search(r"ip=([0-9.]+)", val)
        if m:
            out.append(m.group(1))
    return out


async def _pve_vm_ips(client, base, headers, node, vmid, vmtype) -> list[str]:
    """Best effort: IPs einer laufenden VM/eines Containers. Ohne Gastagent leer."""
    try:
        if vmtype == "lxc":
            cfg = await _get(client, base, f"/api2/json/nodes/{node}/lxc/{vmid}/config", headers)
            return _lxc_ip(cfg)
        data = await _get(client, base,
            f"/api2/json/nodes/{node}/qemu/{vmid}/agent/network-get-interfaces", headers)
        return _ipv4s(data)
    except (httpx.HTTPError, OSError, ValueError, KeyError):
        return []


async def read_proxmox(conf: dict) -> dict[str, dict]:
    """Werte je Node, indiziert nach Node-IP. Fehler ergeben eine leere Karte –
    ein nicht erreichbarer Host darf das Board nie blockieren."""
    if not conf or not conf.get("enabled"):
        return {}
    host = conf.get("host")
    token_id = (conf.get("token_id") or "").strip()
    secret = (conf.get("token_secret") or "").strip()
    if not (host and token_id and secret):
        return {}

    base = _pve_base(host)
    headers = {"Authorization": f"PVEAPIToken={token_id}={secret}"}
    verify = not conf.get("insecure", True)
    out: dict[str, dict] = {}
    try:
        async with httpx.AsyncClient(verify=verify) as client:
            cluster = await _get(client, base, "/api2/json/cluster/status", headers) or []
            # Name -> {ip, online}
            nodes: dict[str, dict] = {}
            for e in cluster:
                if e.get("type") == "node" and e.get("name"):
                    nodes[e["name"]] = {"ip": e.get("ip"), "online": bool(e.get("online", 1))}
            # Einzel-Host ohne Cluster: /cluster/status liefert trotzdem den Node.
            if not nodes:
                for n in (await _get(client, base, "/api2/json/nodes", headers) or []):
                    if n.get("node"):
                        nodes[n["node"]] = {"ip": None, "online": n.get("status") == "online"}

            # VMs/Container je Node einsammeln (für die Detailansicht) und für
            # die VM-Erkennung eine flache Liste mit vmid mitführen.
            vms_by_node: dict[str, list] = {}
            all_vms: list[dict] = []
            for r in (await _get(client, base, "/api2/json/cluster/resources?type=vm", headers) or []):
                node = r.get("node")
                if not node:
                    continue
                maxmem = r.get("maxmem") or 0
                vm = {
                    "name": r.get("name") or f"VM {r.get('vmid')}",
                    "type": "lxc" if r.get("type") == "lxc" else "qemu",
                    "status": r.get("status") or "unknown",
                    "cpu": round((r.get("cpu") or 0) * 100, 1),
                    "mem_pct": round((r.get("mem") or 0) / maxmem * 100, 1) if maxmem else 0,
                }
                vms_by_node.setdefault(node, []).append(vm)
                all_vms.append({**vm, "vmid": r.get("vmid"), "node": node})

            for name, meta in nodes.items():
                ip = meta.get("ip")
                entry = {"kind": "proxmox", "name": name, "status": "online" if meta["online"] else "offline",
                         "cpu": 0.0, "mem_pct": 0.0, "mem_used": 0, "mem_total": 0,
                         "uptime": 0, "vms": sorted(vms_by_node.get(name, []),
                                                    key=lambda v: v["name"].lower())}
                if meta["online"]:
                    try:
                        st = await _get(client, base, f"/api2/json/nodes/{name}/status", headers) or {}
                        mem = st.get("memory") or {}
                        total = mem.get("total") or 0
                        entry.update({
                            "cpu": round((st.get("cpu") or 0) * 100, 1),
                            "mem_used": mem.get("used") or 0,
                            "mem_total": total,
                            "mem_pct": round((mem.get("used") or 0) / total * 100, 1) if total else 0,
                            "uptime": st.get("uptime") or 0,
                        })
                    except (httpx.HTTPError, OSError, ValueError, KeyError):
                        pass
                if ip:
                    out[ip] = entry

            # VM-Erkennung: laufende VMs/Container ihren IPs zuordnen, damit eine
            # gescannte VM ihren Namen und ihre Werte zeigt statt „VMware .19“.
            for vm in all_vms:
                if vm["status"] != "running":
                    continue
                for vip in await _pve_vm_ips(client, base, headers,
                                             vm["node"], vm["vmid"], vm["type"]):
                    out.setdefault(vip, {"kind": "vm", "name": vm["name"],
                        "host": vm["node"], "status": vm["status"],
                        "cpu": vm["cpu"], "mem_pct": vm["mem_pct"]})
    except (httpx.HTTPError, OSError, ValueError, KeyError):
        return {}
    return out


async def test_proxmox(conf: dict) -> dict:
    """Für den „Verbindung testen“-Knopf: klare Rückmeldung statt stiller Leere."""
    host = conf.get("host")
    token_id = (conf.get("token_id") or "").strip()
    secret = (conf.get("token_secret") or "").strip()
    if not host:
        return {"ok": False, "error": "Adresse fehlt."}
    if not token_id or not secret:
        return {"ok": False, "error": "API-Token (ID und Secret) nötig."}
    if "!" not in token_id:
        return {"ok": False, "error": "Token-ID sieht falsch aus – Form: user@pam!name."}
    base = _pve_base(host)
    headers = {"Authorization": f"PVEAPIToken={token_id}={secret}"}
    try:
        async with httpx.AsyncClient(verify=not conf.get("insecure", True)) as client:
            data = await _get(client, base, "/api2/json/cluster/status", headers) or []
        names = [e.get("name") for e in data if e.get("type") == "node"]
        return {"ok": True, "nodes": [n for n in names if n]}
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        if code in (401, 403):
            return {"ok": False, "error": "Token abgelehnt (Rechte/Secret prüfen)."}
        return {"ok": False, "error": f"HTTP {code}."}
    except (httpx.HTTPError, OSError) as exc:
        return {"ok": False, "error": f"Nicht erreichbar: {type(exc).__name__}."}


async def read_all(cfg: dict) -> dict[str, dict]:
    """Alle aktiven Integrationen zu einer Karte IP → Werte zusammenführen."""
    out: dict[str, dict] = {}
    out.update(await read_proxmox(cfg.get("proxmox") or {}))
    # ESXi/vCenter je Host – wenn als Header-Quelle ODER per Schalter für Kacheln.
    vs = cfg.get("vsphere") or {}
    if vs.get("host") and (cfg.get("stats_source") == "vsphere" or vs.get("tiles")):
        try:
            from . import sysinfo
            hosts = await sysinfo.read_vsphere_hosts(vs)
            out.update(hosts or {})
        except Exception:  # pyVmomi darf das Board nie umwerfen
            pass
    return out
