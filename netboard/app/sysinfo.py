"""Systemwerte für die Kopfzeile.

Zwei Quellen:

* ``local`` – liest ``/proc`` des Docker-Hosts. Kostet praktisch nichts und
  braucht keine Einrichtung. Im Host-Netzwerk zeigt ``/proc/stat``,
  ``/proc/meminfo`` und ``/proc/uptime`` die Werte des Hosts, nicht die des
  Containers.
* ``vsphere`` – fragt einen ESXi-Host oder ein vCenter ab. Dort werden die
  Werte aller Hosts eines Clusters zusammengezählt.

Ein Fehler hier darf nie die Oberfläche beeinträchtigen: Alles ist gekapselt,
im Zweifel gibt es schlicht keine Werte.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

PROC = Path(os.getenv("NETBOARD_PROC", "/proc"))

# Letzte CPU-Messung: für die Auslastung braucht es zwei Punkte.
_last: tuple[float, float] | None = None


# --- Lokaler Host ------------------------------------------------------------
def _cpu_sample() -> tuple[float, float] | None:
    try:
        line = (PROC / "stat").read_text().split("\n", 1)[0]
    except OSError:
        return None
    parts = line.split()
    if len(parts) < 5 or parts[0] != "cpu":
        return None
    try:
        vals = [float(v) for v in parts[1:]]
    except ValueError:
        return None
    idle = vals[3] + (vals[4] if len(vals) > 4 else 0)   # idle + iowait
    return sum(vals), idle


def _cpu_percent() -> float | None:
    """Auslastung seit der letzten Messung. Der erste Aufruf liefert nichts."""
    global _last
    cur = _cpu_sample()
    if cur is None:
        return None
    if _last is None:
        _last = cur
        return None
    d_total = cur[0] - _last[0]
    d_idle = cur[1] - _last[1]
    _last = cur
    if d_total <= 0:
        return None
    return max(0.0, min(100.0, (1 - d_idle / d_total) * 100))


def _mem() -> tuple[int, int] | None:
    """(belegt, gesamt) in Bytes."""
    try:
        text = (PROC / "meminfo").read_text()
    except OSError:
        return None
    vals: dict[str, int] = {}
    for line in text.splitlines():
        k, _, rest = line.partition(":")
        if k in ("MemTotal", "MemAvailable", "MemFree"):
            try:
                vals[k] = int(rest.split()[0]) * 1024
            except (ValueError, IndexError):
                pass
    total = vals.get("MemTotal")
    if not total:
        return None
    avail = vals.get("MemAvailable", vals.get("MemFree", 0))
    return max(0, total - avail), total


def _uptime() -> float | None:
    try:
        return float((PROC / "uptime").read_text().split()[0])
    except (OSError, ValueError, IndexError):
        return None


def _load() -> float | None:
    try:
        return float((PROC / "loadavg").read_text().split()[0])
    except (OSError, ValueError, IndexError):
        return None


def read_local() -> dict[str, Any]:
    out: dict[str, Any] = {"source": "local", "name": os.uname().nodename}
    cpu = _cpu_percent()
    if cpu is not None:
        out["cpu"] = round(cpu, 1)
    mem = _mem()
    if mem:
        out["mem_used"], out["mem_total"] = mem
        out["ram"] = round(mem[0] / mem[1] * 100, 1)
    up = _uptime()
    if up is not None:
        out["uptime"] = int(up)
    load = _load()
    if load is not None:
        out["load"] = round(load, 2)
    return out


# --- ESXi / vCenter ----------------------------------------------------------
def read_vsphere(conf: dict[str, Any], with_targets: bool = False) -> dict[str, Any]:
    """Werte eines ESXi-Hosts oder aller Hosts eines Clusters.

    Läuft in einem Thread – pyVmomi spricht blockierendes SOAP.
    Mit ``with_targets`` kommen zusätzlich alle wählbaren Cluster und Hosts
    zurück, damit niemand Namen abtippen muss.
    """
    from . import config as C
    from . import netdns
    from .config import clean_hostport
    # Wer die Adresse aus dem Browser kopiert, bringt „https://…/“ mit.
    host, port = clean_hostport(conf.get("host"))
    if conf.get("port"):
        try:
            port = int(conf["port"])
        except (TypeError, ValueError):
            pass
    user = (conf.get("user") or "").strip()
    pwd = conf.get("password") or ""
    if not host:
        return {"source": "vsphere", "error": "Adresse fehlt."}
    if not user:
        return {"source": "vsphere", "error": "Benutzer fehlt."}
    if not pwd:
        return {"source": "vsphere", "error": "Passwort fehlt."}

    try:
        import ssl
        from pyVim.connect import Disconnect, SmartConnect
        from pyVmomi import vim
    except ImportError:
        return {"source": "vsphere", "error": "pyVmomi ist nicht installiert."}

    ctx = None
    if conf.get("insecure", True):
        ctx = ssl._create_unverified_context()

    # Namen wie „vcenter01.fritz.box“ scheitern im Container oft, weil Docker
    # das lokale DNS des Hosts durch öffentliches ersetzt. Löst das System den
    # Namen nicht auf, fragen wir die Router der eingerichteten Netze direkt.
    conn_host = host
    resolve_failed = False
    if host and not netdns.is_ip(host):
        subnets = C.load().get("subnets") or []
        found = netdns.resolve(host, subnets)
        if found:
            conn_host = found      # per IP verbinden; insecure fängt das Zert. ab
        else:
            resolve_failed = True

    si = None
    try:
        if resolve_failed:
            raise OSError("name or service not known")
        si = SmartConnect(host=conn_host, port=port, user=user, pwd=pwd,
                          sslContext=ctx, connectionPoolTimeout=20)
        content = si.RetrieveContent()
        view = content.viewManager.CreateContainerView(
            content.rootFolder, [vim.HostSystem], True)
        hosts = list(view.view)
        view.Destroy()
        if not hosts:
            return {"source": "vsphere", "error": "Keine Hosts gefunden."}

        # Ein vCenter kennt seinen Typ; ein einzelner ESXi meldet sich anders.
        kind = "vCenter" if getattr(content.about, "apiType", "") == "VirtualCenter" \
               else "ESXi"

        def cluster_of(h):
            parent = getattr(h, "parent", None)
            if isinstance(parent, vim.ClusterComputeResource):
                return parent.name or ""
            return ""

        targets = []
        if with_targets:
            seen = {}
            for h in hosts:
                cl = cluster_of(h)
                if cl:
                    seen.setdefault(("cluster", cl), 0)
                    seen[("cluster", cl)] += 1
            for (k, n), cnt in seen.items():
                targets.append({"name": n, "kind": "Cluster", "hosts": cnt})
            for h in hosts:
                targets.append({"name": h.name or "?", "kind": "Host", "hosts": 1})

        target = (conf.get("target") or "").strip().lower()
        if target:
            picked = [h for h in hosts
                      if target == (h.name or "").lower()
                      or target == cluster_of(h).lower()]
            if not picked:      # Notnagel: Teiltreffer, falls jemand kürzt
                picked = [h for h in hosts
                          if target in (h.name or "").lower()
                          or target in cluster_of(h).lower()]
            if not picked:
                return {"source": "vsphere", "targets": targets,
                        "error": f"„{conf['target']}“ nicht gefunden."}
            hosts = picked

        cpu_used = cpu_cap = 0.0
        mem_used = mem_total = 0
        uptime = 0
        live = 0
        for h in hosts:
            summ = getattr(h, "summary", None)
            if not summ:
                continue
            hw, qs = summ.hardware, summ.quickStats
            if not hw or not qs:
                continue
            live += 1
            cpu_cap += (hw.numCpuCores or 0) * (hw.cpuMhz or 0)
            cpu_used += qs.overallCpuUsage or 0
            mem_total += hw.memorySize or 0
            mem_used += (qs.overallMemoryUsage or 0) * 1024 * 1024
            uptime = max(uptime, qs.uptime or 0)

        if not live or not cpu_cap or not mem_total:
            return {"source": "vsphere", "targets": targets,
                    "error": "Verbunden, aber keine Messwerte erhalten. "
                             "Darf das Konto die Hosts sehen?"}

        name = conf.get("target") or (hosts[0].name if len(hosts) == 1 else host)
        out = {
            "source": "vsphere", "name": name, "hosts": live, "kind": kind,
            "cpu": round(cpu_used / cpu_cap * 100, 1),
            "ram": round(mem_used / mem_total * 100, 1),
            "mem_used": mem_used, "mem_total": mem_total,
            "uptime": int(uptime),
        }
        if with_targets:
            out["targets"] = targets
        return out
    except Exception as exc:            # niemals die Oberfläche mitreißen
        msg = str(exc).split("\n")[0][:120] or type(exc).__name__
        low = msg.lower()
        if "incorrect user name or password" in low or "login" in low:
            msg = "Anmeldung abgelehnt. Benutzer oder Passwort stimmt nicht."
        elif "certificate" in low or "ssl" in low:
            msg = "Zertifikat abgelehnt. „Zertifikat nicht prüfen“ einschalten?"
        elif "timed out" in low or "timeout" in low:
            msg = "Zeitüberschreitung. Ist die Adresse erreichbar?"
        elif "refused" in low or "errno 111" in low:
            msg = "Nicht erreichbar. Läuft dort ein ESXi oder vCenter?"
        elif "name or service not known" in low or "resolve" in low \
                or "getaddrinfo" in low:
            msg = (f"Name „{host}“ ließ sich nicht auflösen. Im Container kennt "
                   "das DNS oft keine lokalen Namen – hier die IP eintragen, oder "
                   "in docker-compose.yml unter dns: den Router (z. B. 192.168.178.1) "
                   "setzen.")
        elif "no route to host" in low or "unreachable" in low:
            msg = "Kein Weg zum Ziel. Stimmt das Netz?"
        elif "not well-formed" in low or "expat" in low or "syntax" in low:
            msg = ("Antwortet nicht wie ein vCenter. Zeigt die Adresse "
                   "vielleicht auf einen Reverse-Proxy?")
        elif "permission" in low or "not authorized" in low:
            msg = "Konto darf das nicht. Es braucht Leserechte auf die Hosts."
        return {"source": "vsphere", "error": msg}
    finally:
        if si is not None:
            try:
                Disconnect(si)
            except Exception:
                pass


def _vsphere_hosts_blocking(conf: dict[str, Any]) -> dict[str, dict]:
    """Werte je ESXi-Host, indiziert nach Host-IP. Läuft blockierend (SOAP) und
    wird deshalb im Executor aufgerufen. Fehler → leere Karte."""
    import socket
    import ssl as _ssl

    from pyVim.connect import Disconnect, SmartConnect
    from pyVmomi import vim

    from . import config as C
    from . import netdns
    from .config import clean_hostport

    host, port = clean_hostport(conf.get("host"))
    if conf.get("port"):
        try:
            port = int(conf["port"])
        except (TypeError, ValueError):
            pass
    user = (conf.get("user") or "").strip()
    pwd = conf.get("password") or ""
    if not (host and user and pwd):
        return {}

    ctx = _ssl._create_unverified_context() if conf.get("insecure", True) else None
    conn_host = host
    if not netdns.is_ip(host):
        conn_host = netdns.resolve(host, C.load().get("subnets") or []) or host

    def host_ip(h) -> str | None:
        cands: list[str] = []
        cn = getattr(getattr(h.summary, "config", None), "name", "") or ""
        if cn:
            cands.append(cn)
        if getattr(h, "name", "") and h.name not in cands:
            cands.append(h.name)
        # Steht schon eine IP als Name da, nehmen wir die.
        for c in cands:
            if netdns.is_ip(c):
                return c
        # Sonst die Management-VMKernel-IP aus der Netzkonfiguration.
        try:
            for vnic in h.config.network.vnic:
                ip = vnic.spec.ip.ipAddress
                if ip and netdns.is_ip(ip):
                    return ip
        except AttributeError:
            pass
        for c in cands:
            try:
                return socket.gethostbyname(c)
            except OSError:
                continue
        return None

    si = None
    out: dict[str, dict] = {}
    try:
        si = SmartConnect(host=conn_host, port=port, user=user, pwd=pwd,
                          sslContext=ctx, connectionPoolTimeout=20)
        content = si.RetrieveContent()
        view = content.viewManager.CreateContainerView(
            content.rootFolder, [vim.HostSystem], True)
        hosts = list(view.view)
        view.Destroy()
        for h in hosts:
            summ = getattr(h, "summary", None)
            if not summ or not summ.hardware or not summ.quickStats:
                continue
            hw, qs = summ.hardware, summ.quickStats
            cap = (hw.numCpuCores or 0) * (hw.cpuMhz or 0)
            mtot = hw.memorySize or 0
            mused = (qs.overallMemoryUsage or 0) * 1024 * 1024
            connected = getattr(summ.runtime, "connectionState", "") == "connected"
            vms = []
            vm_ips: list[tuple[str, dict]] = []
            for vm in (getattr(h, "vm", None) or [])[:40]:
                try:
                    vsum = vm.summary
                    vqs = vsum.quickStats
                    vmax = (vsum.config.memorySizeMB or 0) * 1024 * 1024
                    rec = {
                        "name": vsum.config.name or "?",
                        "type": "vm",
                        "status": vsum.runtime.powerState or "unknown",
                        "cpu": round((vqs.overallCpuUsage or 0) / cap * 100, 1) if cap else 0,
                        "mem_pct": round((vqs.guestMemoryUsage or 0) * 1024 * 1024 / vmax * 100, 1) if vmax else 0,
                    }
                    vms.append(rec)
                    # Gast-IP(s) über VMware Tools – damit eine gescannte VM ihren
                    # Namen und ihre Werte zeigt statt „VMware .19“.
                    gips: list[str] = []
                    prim = getattr(getattr(vsum, "guest", None), "ipAddress", None)
                    if prim:
                        gips.append(prim)
                    for nic in (getattr(getattr(vm, "guest", None), "net", None) or []):
                        for a in (getattr(nic, "ipAddress", None) or []):
                            if a and ":" not in a:
                                gips.append(a)
                    for gip in gips:
                        if gip and not gip.startswith("127."):
                            vm_ips.append((gip, {"kind": "vm", "name": rec["name"],
                                "host": (summ.config.name if summ.config else None) or h.name,
                                "status": rec["status"], "cpu": rec["cpu"], "mem_pct": rec["mem_pct"]}))
                except AttributeError:
                    continue
            ip = host_ip(h)
            if not ip:
                continue
            out[ip] = {
                "kind": "esxi",
                "name": (summ.config.name if summ.config else None) or h.name or ip,
                "status": "online" if connected else "offline",
                "cpu": round((qs.overallCpuUsage or 0) / cap * 100, 1) if cap else 0,
                "mem_pct": round(mused / mtot * 100, 1) if mtot else 0,
                "mem_used": mused, "mem_total": mtot,
                "uptime": qs.uptime or 0,
                "vms": sorted(vms, key=lambda v: v["name"].lower()),
            }
            for gip, rec in vm_ips:
                out.setdefault(gip, rec)
    except Exception:
        return out
    finally:
        if si is not None:
            try:
                Disconnect(si)
            except Exception:
                pass
    return out


async def read_vsphere_hosts(conf: dict[str, Any]) -> dict[str, dict]:
    """Async-Hülle: die blockierende SOAP-Arbeit läuft in einem Thread."""
    import asyncio
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _vsphere_hosts_blocking, conf)


def read(cfg: dict[str, Any]) -> dict[str, Any] | None:
    src = cfg.get("stats_source", "local")
    if src == "off":
        return None
    if src == "vsphere":
        return read_vsphere(cfg.get("vsphere") or {})
    return read_local()
