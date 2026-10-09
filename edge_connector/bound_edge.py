#!/usr/bin/env python3
"""BOUND school edge connector.

Outbound-only, typed, read-only infrastructure collector. It keeps school-side
credentials local and polls BOUND for narrowly-scoped inspection jobs.

The connector intentionally has no generic shell action.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import socket
import ssl
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import paramiko
import requests
import winrm
from ldap3 import ALL, Connection, Server, SUBTREE
from onvif import ONVIFCamera


VERSION = "0.1.0"
CONFIG_PATH = Path(os.getenv("BOUND_EDGE_CONFIG", "/etc/bound-edge.json"))


class EdgeError(RuntimeError):
    pass


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def load_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        raise EdgeError(f"Missing config: {CONFIG_PATH}")
    data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    if not isinstance(data.get("scopes"), dict) or not data["scopes"]:
        raise EdgeError("At least one named network scope is required")
    return data


CONFIG = load_config()
SCOPES = {
    name: ipaddress.ip_network(value, strict=False)
    for name, value in CONFIG["scopes"].items()
}
APPROVED_PORTS = {int(p) for p in CONFIG.get("approved_tcp_ports", [])}
APPROVED_OIDS = tuple(str(x).lstrip(".") for x in CONFIG.get("approved_snmp_oids", []))
MAX_RESULT_BYTES = int(CONFIG.get("max_result_bytes", 524288))


def headers() -> dict[str, str]:
    token = env("BOUND_EDGE_TOKEN")
    if not token:
        raise EdgeError("BOUND_EDGE_TOKEN is not configured")
    result = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": f"bound-school-edge/{VERSION}",
    }
    cf_id = env("CF_ACCESS_CLIENT_ID")
    cf_secret = env("CF_ACCESS_CLIENT_SECRET")
    if cf_id and cf_secret:
        result["CF-Access-Client-Id"] = cf_id
        result["CF-Access-Client-Secret"] = cf_secret
    return result


def base_url() -> str:
    value = env("BOUND_URL").rstrip("/")
    if not value.startswith("https://"):
        raise EdgeError("BOUND_URL must use HTTPS")
    return value


def scope_network(name: str):
    try:
        return SCOPES[name]
    except KeyError as exc:
        raise EdgeError(f"Unknown scope: {name}") from exc


def resolve_target(value: str) -> str:
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        try:
            candidates = socket.getaddrinfo(value, None)
        except socket.gaierror as exc:
            raise EdgeError(f"Could not resolve target: {value}") from exc
        addresses = []
        for item in candidates:
            raw = item[4][0]
            try:
                addresses.append(ipaddress.ip_address(raw))
            except ValueError:
                continue
        if not addresses:
            raise EdgeError("Target resolved to no usable IP address")
        ip = addresses[0]

    if not any(ip in network for network in SCOPES.values()):
        raise EdgeError(f"Target {ip} is outside configured school scopes")
    return str(ip)


def trim(value: Any) -> Any:
    raw = json.dumps(value, default=str, ensure_ascii=False)
    encoded = raw.encode("utf-8")
    if len(encoded) <= MAX_RESULT_BYTES:
        return value
    return {
        "truncated": True,
        "original_bytes": len(encoded),
        "preview": raw[: max(1000, MAX_RESULT_BYTES // 2)],
    }


def ping(target: str) -> dict:
    ip = resolve_target(target)
    completed = subprocess.run(
        ["ping", "-c", "1", "-W", "2", ip],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    return {
        "target": ip,
        "reachable": completed.returncode == 0,
        "output": completed.stdout[-1200:],
    }


def tcp_check(target: str, port: int) -> dict:
    ip = resolve_target(target)
    port = int(port)
    if port not in APPROVED_PORTS:
        raise EdgeError(f"Port {port} is not approved by local policy")
    started = time.monotonic()
    try:
        with socket.create_connection((ip, port), timeout=3):
            open_state = True
            error = None
    except OSError as exc:
        open_state = False
        error = str(exc)
    return {
        "target": ip,
        "port": port,
        "open": open_state,
        "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
        "error": error,
    }


def discover(scope: str, ports: list[int] | None = None) -> dict:
    network = scope_network(scope)
    ports = [int(p) for p in (ports or [22, 80, 443])]
    for port in ports:
        if port not in APPROVED_PORTS:
            raise EdgeError(f"Port {port} is not approved by local policy")

    hosts = list(network.hosts())
    if len(hosts) > 1024:
        raise EdgeError("A single discovery job may inspect at most 1024 addresses")

    def inspect(ip):
        found = []
        for port in ports:
            try:
                with socket.create_connection((str(ip), port), timeout=0.35):
                    found.append(port)
            except OSError:
                pass
        return {"ip": str(ip), "open_ports": found} if found else None

    found = []
    with ThreadPoolExecutor(max_workers=48) as pool:
        futures = [pool.submit(inspect, ip) for ip in hosts]
        for future in as_completed(futures):
            item = future.result()
            if item:
                found.append(item)

    found.sort(key=lambda x: ipaddress.ip_address(x["ip"]))
    return {
        "scope": scope,
        "network": str(network),
        "ports": ports,
        "hosts": found,
        "count": len(found),
    }


SSH_PROFILES = {
    "mikrotik_routeros": {
        "interfaces": "/interface print terse",
        "addresses": "/ip address print terse",
        "routes": "/ip route print terse",
        "bridge_vlans": "/interface bridge vlan print terse",
        "bridge_ports": "/interface bridge port print terse",
        "neighbors": "/ip neighbor print terse",
        "dhcp_leases": "/ip dhcp-server lease print terse",
    },
    "generic_network_cli": {
        "interfaces": "show interfaces",
        "vlans": "show vlan",
        "routes": "show ip route",
        "neighbors": "show lldp neighbors",
        "mac_table": "show mac address-table",
    },
    "arista_eos": {
        "interfaces": "show interfaces status",
        "vlans": "show vlan",
        "routes": "show ip route",
        "neighbors": "show lldp neighbors detail",
        "mac_table": "show mac address-table dynamic",
    },
}


def ssh_profile(target: str, profile: str, operation: str) -> dict:
    ip = resolve_target(target)
    command = SSH_PROFILES.get(profile, {}).get(operation)
    if not command:
        raise EdgeError("Unknown SSH profile or operation")

    user = env("EDGE_SSH_USERNAME")
    password = env("EDGE_SSH_PASSWORD")
    if not user or not password:
        raise EdgeError("EDGE_SSH_USERNAME/PASSWORD are not configured")

    client = paramiko.SSHClient()
    client.load_system_host_keys()
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    try:
        client.connect(
            ip,
            port=int(env("EDGE_SSH_PORT", "22")),
            username=user,
            password=password,
            look_for_keys=False,
            allow_agent=False,
            timeout=5,
            auth_timeout=8,
        )
        _stdin, stdout, stderr = client.exec_command(command, timeout=15)
        return {
            "target": ip,
            "profile": profile,
            "operation": operation,
            "stdout": stdout.read().decode("utf-8", "replace")[:100000],
            "stderr": stderr.read().decode("utf-8", "replace")[:10000],
        }
    finally:
        client.close()


def snmp_walk(target: str, oid: str) -> dict:
    ip = resolve_target(target)
    oid = str(oid).lstrip(".")
    if not any(oid == base or oid.startswith(base + ".") for base in APPROVED_OIDS):
        raise EdgeError("OID is outside the locally approved SNMP trees")
    community = env("EDGE_SNMP_COMMUNITY")
    if not community:
        raise EdgeError("EDGE_SNMP_COMMUNITY is not configured")
    completed = subprocess.run(
        ["snmpwalk", "-v2c", "-c", community, "-t", "2", "-r", "1", ip, oid],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    return {
        "target": ip,
        "oid": oid,
        "returncode": completed.returncode,
        "stdout": completed.stdout[:100000],
        "stderr": completed.stderr[:10000],
    }


def ad_connection() -> Connection:
    uri = env("AD_LDAP_URI")
    bind_dn = env("AD_BIND_DN")
    password = env("AD_BIND_PASSWORD")
    if not uri or not bind_dn or not password:
        raise EdgeError("AD LDAP credentials are not configured")
    use_ssl = uri.lower().startswith("ldaps://")
    host = re.sub(r"^ldaps?://", "", uri).split(":", 1)[0]
    server = Server(host, use_ssl=use_ssl, get_info=ALL, connect_timeout=8)
    conn = Connection(server, user=bind_dn, password=password, auto_bind=True)
    return conn


AD_SEARCHES = {
    "users": ("(&(objectCategory=person)(objectClass=user))", ["sAMAccountName", "displayName", "userAccountControl"]),
    "computers": ("(objectCategory=computer)", ["dNSHostName", "operatingSystem", "operatingSystemVersion"]),
    "groups": ("(objectCategory=group)", ["cn", "sAMAccountName", "groupType"]),
    "domain_controllers": ("(&(objectCategory=computer)(userAccountControl:1.2.840.113556.1.4.803:=8192))", ["dNSHostName", "operatingSystem"]),
}


def ad_search(kind: str, query: str | None = None, limit: int = 200) -> dict:
    if kind not in AD_SEARCHES:
        raise EdgeError("Unsupported AD search type")
    base = env("AD_BASE_DN")
    if not base:
        raise EdgeError("AD_BASE_DN is not configured")
    search_filter, attrs = AD_SEARCHES[kind]
    if query:
        safe = re.sub(r"[^A-Za-z0-9 ._@-]", "", query)[:120]
        if kind == "users":
            search_filter = f"(&(objectCategory=person)(objectClass=user)(|(sAMAccountName=*{safe}*)(displayName=*{safe}*)))"
        elif kind == "computers":
            search_filter = f"(&(objectCategory=computer)(|(name=*{safe}*)(dNSHostName=*{safe}*)))"
        elif kind == "groups":
            search_filter = f"(&(objectCategory=group)(|(cn=*{safe}*)(sAMAccountName=*{safe}*)))"

    conn = ad_connection()
    try:
        conn.search(base, search_filter, SUBTREE, attributes=attrs, size_limit=min(int(limit), 500))
        entries = [json.loads(entry.entry_to_json()) for entry in conn.entries]
        return {"kind": kind, "count": len(entries), "entries": entries}
    finally:
        conn.unbind()


def ad_summary() -> dict:
    result = {}
    for kind in ("domain_controllers", "users", "computers", "groups"):
        data = ad_search(kind, limit=500)
        result[kind] = {
            "count_returned": data["count"],
            "entries": data["entries"] if kind == "domain_controllers" else None,
        }
    return result


WINDOWS_READ_PROFILES = {
    "ad_replication": "Get-ADReplicationPartnerMetadata -Target * -Scope Domain | Select-Object Server,Partner,LastReplicationSuccess,LastReplicationResult | ConvertTo-Json -Depth 4",
    "dns_server": "Get-DnsServerZone | Select-Object ZoneName,ZoneType,IsDsIntegrated,IsReverseLookupZone | ConvertTo-Json -Depth 4",
    "dhcp_scopes": "Get-DhcpServerv4Scope | Select-Object ScopeId,Name,State,StartRange,EndRange,SubnetMask | ConvertTo-Json -Depth 4",
    "nps_recent": "Get-WinEvent -FilterHashtable @{LogName='Security';Id=6272,6273;StartTime=(Get-Date).AddHours(-6)} -MaxEvents 200 | Select-Object TimeCreated,Id,Message | ConvertTo-Json -Depth 4",
    "services": "Get-Service | Select-Object Name,DisplayName,Status,StartType | ConvertTo-Json -Depth 3",
}


def windows_read_profile(profile: str) -> dict:
    script = WINDOWS_READ_PROFILES.get(profile)
    if not script:
        raise EdgeError("Unknown Windows read profile")
    endpoint = env("WINRM_ENDPOINT")
    username = env("WINRM_USERNAME")
    password = env("WINRM_PASSWORD")
    if not endpoint or not username or not password:
        raise EdgeError("WinRM is not configured")
    session = winrm.Session(
        endpoint,
        auth=(username, password),
        transport=env("WINRM_TRANSPORT", "ntlm"),
        server_cert_validation="validate" if env("WINRM_VERIFY_TLS", "true").lower() == "true" else "ignore",
    )
    response = session.run_ps(script)
    return {
        "profile": profile,
        "status_code": response.status_code,
        "stdout": response.std_out.decode("utf-8", "replace")[:100000],
        "stderr": response.std_err.decode("utf-8", "replace")[:10000],
    }


def unifi_request(resource: str) -> dict:
    base = env("UNIFI_BASE_URL").rstrip("/")
    if not base.startswith("https://"):
        raise EdgeError("UNIFI_BASE_URL must use HTTPS")
    site = CONFIG.get("unifi", {}).get("site", "default")
    verify = env("UNIFI_VERIFY_TLS", "true").lower() == "true"
    session = requests.Session()
    session.verify = verify
    api_key = env("UNIFI_API_KEY")
    if api_key:
        session.headers["X-API-Key"] = api_key
    else:
        username = env("UNIFI_USERNAME")
        password = env("UNIFI_PASSWORD")
        if not username or not password:
            raise EdgeError("UniFi API credentials are not configured")
        login_payload = {"username": username, "password": password, "remember": True}
        logged_in = False
        for path in ("/api/auth/login", "/api/login"):
            response = session.post(base + path, json=login_payload, timeout=12)
            if response.ok:
                logged_in = True
                break
        if not logged_in:
            raise EdgeError("UniFi controller login failed")

    paths = {
        "devices": [
            f"/proxy/network/api/s/{site}/stat/device",
            f"/api/s/{site}/stat/device",
        ],
        "clients": [
            f"/proxy/network/api/s/{site}/stat/sta",
            f"/api/s/{site}/stat/sta",
        ],
        "health": [
            f"/proxy/network/api/s/{site}/stat/health",
            f"/api/s/{site}/stat/health",
        ],
    }
    if resource not in paths:
        raise EdgeError("Unsupported UniFi resource")

    last = None
    for path in paths[resource]:
        response = session.get(base + path, timeout=15)
        last = response
        if response.ok:
            payload = response.json()
            return {
                "resource": resource,
                "path": path,
                "data": payload.get("data", payload),
            }
    raise EdgeError(f"UniFi API request failed: HTTP {last.status_code if last else 'unknown'}")


def grandstream_request(resource: str) -> dict:
    cfg = CONFIG.get("grandstream", {})
    path = cfg.get(f"{resource}_path", "")
    if not path:
        raise EdgeError(
            f"Grandstream {resource} API path is not configured for this controller version"
        )
    base = env("GWN_BASE_URL").rstrip("/")
    token = env("GWN_API_TOKEN")
    if not base.startswith("https://") or not token:
        raise EdgeError("GWN_BASE_URL and GWN_API_TOKEN are required")
    response = requests.get(
        base + "/" + path.lstrip("/"),
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
        verify=env("GWN_VERIFY_TLS", "true").lower() == "true",
    )
    response.raise_for_status()
    return {"resource": resource, "data": response.json()}


def onvif_info(target: str) -> dict:
    ip = resolve_target(target)
    username = env("ONVIF_USERNAME")
    password = env("ONVIF_PASSWORD")
    if not username or not password:
        raise EdgeError("ONVIF credentials are not configured")
    camera = ONVIFCamera(ip, int(env("ONVIF_PORT", "80")), username, password)
    device = camera.create_devicemgmt_service()
    info = device.GetDeviceInformation()
    services = device.GetServices({"IncludeCapability": False})
    return {
        "target": ip,
        "manufacturer": getattr(info, "Manufacturer", None),
        "model": getattr(info, "Model", None),
        "firmware": getattr(info, "FirmwareVersion", None),
        "serial_number": getattr(info, "SerialNumber", None),
        "hardware_id": getattr(info, "HardwareId", None),
        "services": [
            {
                "namespace": getattr(item, "Namespace", None),
                "xaddr": getattr(item, "XAddr", None),
            }
            for item in services
        ],
    }


def ami_status(target: str) -> dict:
    ip = resolve_target(target)
    user = env("PBX_AMI_USERNAME")
    secret = env("PBX_AMI_SECRET")
    port = int(env("PBX_AMI_PORT", "5038"))
    if port not in APPROVED_PORTS:
        raise EdgeError("PBX AMI port is not approved locally")
    if not user or not secret:
        raise EdgeError("PBX AMI credentials are not configured")

    with socket.create_connection((ip, port), timeout=5) as sock:
        stream = sock.makefile("rwb", buffering=0)
        stream.readline()
        payload = (
            f"Action: Login\r\nUsername: {user}\r\nSecret: {secret}\r\nEvents: off\r\n\r\n"
            "Action: CoreStatus\r\nActionID: bound-core-status\r\n\r\n"
            "Action: Logoff\r\n\r\n"
        ).encode()
        stream.write(payload)
        sock.settimeout(5)
        chunks = []
        try:
            while True:
                data = sock.recv(8192)
                if not data:
                    break
                chunks.append(data)
                if b"Goodbye" in data:
                    break
        except socket.timeout:
            pass

    text = b"".join(chunks).decode("utf-8", "replace")
    # Never return the login secret. AMI responses do not normally echo it,
    # but redact defensively.
    text = text.replace(secret, "[REDACTED]")
    return {"target": ip, "status": text[:50000]}


def execute(capability: str, target: str | None, parameters: dict[str, Any]) -> Any:
    if capability == "network.discover":
        return discover(str(parameters["scope"]), parameters.get("ports"))
    if capability == "network.ping":
        return ping(target or str(parameters["target"]))
    if capability == "network.tcp":
        return tcp_check(target or str(parameters["target"]), int(parameters["port"]))
    if capability == "network.ssh_profile":
        return ssh_profile(target or str(parameters["target"]), str(parameters["profile"]), str(parameters["operation"]))
    if capability in {"network.snmp_walk", "grandstream.snmp_walk"}:
        return snmp_walk(target or str(parameters["target"]), str(parameters["oid"]))
    if capability == "ad.directory_summary":
        return ad_summary()
    if capability == "ad.search":
        return ad_search(str(parameters["kind"]), parameters.get("query"), int(parameters.get("limit", 200)))
    if capability == "windows.read_profile":
        return windows_read_profile(str(parameters["profile"]))
    if capability.startswith("unifi."):
        return unifi_request(capability.split(".", 1)[1])
    if capability.startswith("grandstream.") and capability != "grandstream.snmp_walk":
        return grandstream_request(capability.split(".", 1)[1])
    if capability == "cctv.onvif_info":
        return onvif_info(target or str(parameters["target"]))
    if capability == "cctv.tcp_status":
        ports = parameters.get("ports", [80, 443, 554])
        return {
            "target": target,
            "ports": [tcp_check(target or str(parameters["target"]), int(port)) for port in ports],
        }
    if capability == "pbx.ami_status":
        return ami_status(target or str(parameters["target"]))
    if capability == "pbx.tcp_status":
        ports = parameters.get("ports", [5060, 5061, 5038])
        return {
            "target": target,
            "ports": [tcp_check(target or str(parameters["target"]), int(port)) for port in ports],
        }
    raise EdgeError(f"Unsupported capability: {capability}")


CAPABILITIES = [
    "network.discover",
    "network.ping",
    "network.tcp",
    "network.ssh_profile",
    "network.snmp_walk",
    "ad.directory_summary",
    "ad.search",
    "windows.read_profile",
    "unifi.devices",
    "unifi.clients",
    "unifi.health",
    "grandstream.devices",
    "grandstream.clients",
    "grandstream.snmp_walk",
    "cctv.onvif_info",
    "cctv.tcp_status",
    "pbx.ami_status",
    "pbx.tcp_status",
]


def api(method: str, path: str, **kwargs):
    response = requests.request(
        method,
        base_url() + path,
        headers=headers(),
        timeout=int(CONFIG.get("request_timeout_seconds", 20)),
        **kwargs,
    )
    response.raise_for_status()
    return response.json() if response.content else {}


def heartbeat():
    return api(
        "POST",
        "/edge/v1/heartbeat",
        json={
            "connector_id": CONFIG.get("connector_id", "school-edge-01"),
            "environment_id": CONFIG.get("environment_id", "school"),
            "display_name": CONFIG.get("display_name", "School Edge"),
            "version": VERSION,
            "capabilities": CAPABILITIES,
        },
    )


def work_once():
    connector_id = CONFIG.get("connector_id", "school-edge-01")
    payload = api("GET", "/edge/v1/jobs/next", params={"connector_id": connector_id})
    job = payload.get("job")
    if not job:
        return False

    observed_at = datetime.now(timezone.utc).isoformat()
    try:
        result = trim(
            execute(
                str(job["capability"]),
                job.get("target"),
                job.get("parameters") or {},
            )
        )
        body = {
            "connector_id": connector_id,
            "lease_token": str(job["lease_token"]),
            "ok": True,
            "result": result,
            "error": None,
            "observed_at": observed_at,
        }
    except Exception as exc:
        body = {
            "connector_id": connector_id,
            "lease_token": str(job["lease_token"]),
            "ok": False,
            "result": None,
            "error": f"{type(exc).__name__}: {exc}"[:4000],
            "observed_at": observed_at,
        }

    api("POST", f"/edge/v1/jobs/{job['id']}/result", json=body)
    return True


def main():
    heartbeat_at = 0.0
    poll = max(2, int(CONFIG.get("poll_seconds", 5)))
    while True:
        try:
            if time.monotonic() >= heartbeat_at:
                heartbeat()
                heartbeat_at = time.monotonic() + 60
            did_work = work_once()
            time.sleep(0.5 if did_work else poll)
        except KeyboardInterrupt:
            return
        except Exception as exc:
            print(f"{datetime.now(timezone.utc).isoformat()} connector error: {exc}", file=sys.stderr)
            time.sleep(min(30, poll * 2))


if __name__ == "__main__":
    main()
