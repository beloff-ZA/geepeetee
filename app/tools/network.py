import socket
import subprocess

from app.tools.registry import register_tool
from app.security.policy import ToolPolicy, RiskLevel


@register_tool(
    name="dns_lookup",
    description="Resolve a hostname to IP addresses",
    policy=ToolPolicy(
        name="dns_lookup",
        risk=RiskLevel.READ,
        approval_required=False,
        allowed_environments=[
            "core",
            "home",
            "mbl",
            "client",
        ],
        allow_arbitrary_input=False,
    ),
)
def dns_lookup(hostname: str):

    results = socket.getaddrinfo(
        hostname,
        None,
    )

    addresses = sorted(
        {
            result[4][0]
            for result in results
        }
    )

    return {
        "hostname": hostname,
        "addresses": addresses,
    }


@register_tool(
    name="ping_host",
    description="Ping a single host",
    policy=ToolPolicy(
        name="ping_host",
        risk=RiskLevel.READ,
        approval_required=False,
        allowed_environments=[
            "core",
            "home",
            "mbl",
            "client",
        ],
        allow_arbitrary_input=False,
        max_targets=1,
    ),
)
def ping_host(host: str):

    result = subprocess.run(
        [
            "ping",
            "-c",
            "4",
            "-W",
            "2",
            host,
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )

    return {
        "host": host,
        "returncode": result.returncode,
        "stdout": result.stdout[-4000:],
        "stderr": result.stderr[-1000:],
    }

@register_tool(
    name="restart_test_service",
    description="Restart an approved test service",
    policy=ToolPolicy(
        name="restart_test_service",
        risk=RiskLevel.SAFE_WRITE,
        approval_required=True,
        allowed_environments=[
            "core",
        ],
        allow_arbitrary_input=False,
        requires_target=True,
    ),
)
def restart_test_service(target: str):

    allowed_services = {
        "bound-test",
    }

    if target not in allowed_services:
        raise ValueError(
            "Service is not approved for restart"
        )

    result = subprocess.run(
        [
            "sudo",
            "systemctl",
            "restart",
            target,
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )

    return {
        "target": target,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }
