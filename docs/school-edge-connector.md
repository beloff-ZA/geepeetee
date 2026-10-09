# BOUND School Edge Connector

The school edge connector is an outbound-only collector that runs inside the
school network and gives BOUND typed, read-only visibility without exposing an
inbound management port.

## Design

```
BOUND
  |
  | HTTPS jobs/results
  v
Cloudflare Access / Tunnel
  ^
  | outbound polling only
  |
School Edge Connector
  +-- LDAP/LDAPS -> Active Directory
  +-- WinRM -> AD/DNS/DHCP/NPS Windows servers
  +-- HTTPS -> UniFi controller
  +-- HTTPS/SNMP -> Grandstream/GWN
  +-- SSH/SNMP -> switches, routers and supported firewall CLIs
  +-- ONVIF -> cameras/NVR metadata
  +-- AMI/TCP -> supported PBX status
  +-- bounded TCP discovery -> locally configured network scopes
```

School credentials stay on the edge host. Jobs sent by BOUND contain only a
typed capability, target and non-secret parameters. There is deliberately no
generic shell, PowerShell, Python, SQL or arbitrary command capability.

## Required school-side access

Use dedicated read-only accounts wherever the platform supports them.

| System | Preferred access | Purpose |
| --- | --- | --- |
| Active Directory | LDAPS + read-only domain account | users, groups, computers, controllers |
| Windows AD/DNS/DHCP/NPS | WinRM + constrained/read-only service account | replication, DNS zones, DHCP scopes, NPS events, service health |
| UniFi Network | HTTPS API + read-only/local API account or API key | AP/switch inventory, clients, health |
| Grandstream/GWN | HTTPS controller API where available; SNMP for switching | inventory, clients, switch state |
| MikroTik | SSH read-only group and/or SNMP | interfaces, routes, bridges, VLANs, neighbors, leases |
| Firewall/network CLI | dedicated read-only SSH account and/or vendor API | interfaces, routing, policy/status evidence |
| CCTV/NVR | ONVIF read-only user; management TCP checks | model/firmware/service metadata without requesting streams |
| PBX | read-only Asterisk AMI account where compatible; management TCP checks | PBX health without call control |
| Linux infrastructure | SSH read-only account | fixed inspection profiles only |

## BOUND agent skill mapping

- Operator: cross-system triage and school edge inspection orchestration.
- Network: discovery, routing, VLAN, DHCP/DNS, SNMP, SSH profiles, UniFi,
  Grandstream/GWN, MikroTik and firewall path analysis.
- Identity & Access: AD LDAP, Windows Server, NPS/RADIUS, DNS/DHCP and WinRM
  read profiles.
- Endpoint & MDM: endpoint inventory correlation and wireless client state.
- Software: service health, controller/API integration and PBX dependencies.
- CCTV & Physical Systems: ONVIF inventory, NVR/camera reachability and
  retention/network dependencies.
- Problem Solver: bounded cross-domain inspection and evidence-driven fault
  isolation.

Only agents with `PROPOSE_ACTION` authority and `school_edge_read` in their
allowlist may propose these inspections. Agents still cannot execute tools
directly. Normal BOUND gates and audit records remain authoritative.

## Local network scope

Do not commit the real school subnets to this public repository.

Copy `edge_connector/config.example.json` to `/etc/bound-edge.json` on the
school connector and replace the documentation/example networks with the
operator-approved local scopes.

The connector resolves every target to an IP address and rejects it unless the
address is inside one of those configured networks. Discovery jobs reference a
named scope rather than accepting an arbitrary CIDR from BOUND.

## Cloudflare machine authentication

The human UI can stay protected by Google login. The connector should use a
Cloudflare Access service token for the machine path:

```
/edge/v1/*
```

Configure the service-token ID and secret on the school host as:

```
CF_ACCESS_CLIENT_ID=...
CF_ACCESS_CLIENT_SECRET=...
```

BOUND also validates its own connector bearer token. Configure the same random
secret on both ends as `BOUND_SCHOOL_EDGE_TOKEN` / `BOUND_EDGE_TOKEN`.
Cloudflare identity and BOUND connector identity are separate layers.

## Installation

Recommended host: a small Linux VM on the school management network with routes
to the approved school infrastructure ranges.

```bash
sudo useradd --system --home /var/lib/bound-edge --shell /usr/sbin/nologin bound-edge
sudo mkdir -p /opt/bound-edge /var/lib/bound-edge
sudo cp edge_connector/bound_edge.py /opt/bound-edge/
sudo cp edge_connector/config.example.json /etc/bound-edge.json
sudo python3 -m venv /opt/bound-edge/venv
sudo /opt/bound-edge/venv/bin/pip install -r edge_connector/requirements.txt
sudo apt-get install -y iputils-ping snmp
sudo chown -R bound-edge:bound-edge /opt/bound-edge /var/lib/bound-edge
sudo chmod 640 /etc/bound-edge.json
```

Create `/etc/bound-edge.env`:

```dotenv
BOUND_URL=https://bots.example.org
BOUND_EDGE_TOKEN=
CF_ACCESS_CLIENT_ID=
CF_ACCESS_CLIENT_SECRET=

AD_LDAP_URI=ldaps://dc.example.org
AD_BASE_DN=DC=example,DC=org
AD_BIND_DN=
AD_BIND_PASSWORD=

WINRM_ENDPOINT=https://dc.example.org:5986/wsman
WINRM_USERNAME=
WINRM_PASSWORD=
WINRM_TRANSPORT=ntlm
WINRM_VERIFY_TLS=true

UNIFI_BASE_URL=https://unifi.example.org
UNIFI_API_KEY=
UNIFI_USERNAME=
UNIFI_PASSWORD=
UNIFI_VERIFY_TLS=true

GWN_BASE_URL=https://gwn.example.org
GWN_API_TOKEN=
GWN_VERIFY_TLS=true

EDGE_SSH_USERNAME=
EDGE_SSH_PASSWORD=
EDGE_SSH_PORT=22
EDGE_SNMP_COMMUNITY=

ONVIF_USERNAME=
ONVIF_PASSWORD=
ONVIF_PORT=80

PBX_AMI_USERNAME=
PBX_AMI_SECRET=
PBX_AMI_PORT=5038
```

Protect it:

```bash
sudo chown root:bound-edge /etc/bound-edge.env /etc/bound-edge.json
sudo chmod 640 /etc/bound-edge.env /etc/bound-edge.json
sudo cp edge_connector/bound-edge.service /etc/systemd/system/bound-edge.service
sudo systemctl daemon-reload
sudo systemctl enable --now bound-edge
```

## First inspection sequence

1. Confirm connector heartbeat in `GET /connectors`.
2. Run a narrow `network.discover` job against one named scope.
3. Configure and validate AD LDAP.
4. Configure WinRM read profiles for AD/DNS/DHCP/NPS.
5. Configure UniFi API access.
6. Configure Grandstream controller API paths for the installed GWN version and
   SNMP for switches.
7. Add read-only SSH/SNMP accounts to core switching/firewall infrastructure.
8. Configure ONVIF read accounts for CCTV/NVR management.
9. Configure PBX read-only status access.
10. Feed successful observations back into BOUND as evidence before expanding
    scope.

## Explicit non-goals in phase one

- No arbitrary shell or PowerShell supplied by models.
- No device configuration changes.
- No password resets, account changes or group membership writes.
- No firewall, VLAN, switch-port, Wi-Fi or PBX writes.
- No CCTV stream retrieval.
- No credential export to BOUND.
- No unrestricted subnet scanning outside the local allowlist.

Write-capable actions can be added later as separate typed capabilities with
human approval, rollback metadata and stricter per-agent authorization.
