"""Nothing in the dev stack may be published on all interfaces.

Every port here was once on 0.0.0.0, which on any shared network — a cafe, an
office, a flatmate's laptop — put Redis, Postgres, Grafana and the gateway
itself in reach of anyone on the same subnet. Redis mattered most: it holds
the rate-limit counters and the spend ledger, so an unauthenticated peer could
erase both controls on what this gateway is allowed to spend.

That was fixed by adding a 127.0.0.1 prefix to each mapping. It is one prefix
per line, trivially lost to a copy-paste from any tutorial, and the loss is
invisible locally — everything still works, just for more people.
"""

import pathlib
import re

COMPOSE = (pathlib.Path(__file__).resolve().parent.parent / "docker-compose.yml").read_text()


def _published_ports() -> list[str]:
    """Every `- "host:container"` mapping under a ports: block."""
    found = []
    in_ports = False
    for line in COMPOSE.splitlines():
        if re.match(r"\s*ports:\s*$", line):
            in_ports = True
            continue
        if in_ports:
            m = re.match(r'\s*-\s*"([^"]+)"', line)
            if m:
                found.append(m.group(1))
                continue
            if line.strip() and not line.strip().startswith("#"):
                in_ports = False
    return found


def test_every_published_port_binds_loopback():
    ports = _published_ports()
    assert ports, "no published ports found — the guard would pass vacuously"

    exposed = [p for p in ports if not p.startswith("127.0.0.1:")]
    assert not exposed, (
        f"{exposed} are published on all interfaces. Anyone on the same"
        " network reaches them — including Redis, which holds the rate-limit"
        " counters and the lifetime spend ledger."
    )


def _gateway_service() -> str:
    """The gateway service block, up to the next top-level service."""
    match = re.search(r"^  gateway:\n(.*?)(?=^  \w+:)", COMPOSE, re.M | re.S)
    assert match, "no gateway service found in docker-compose.yml"
    return match.group(1)


def test_the_gateway_container_cannot_escalate_privileges():
    """The image already runs as an unprivileged uid; this is what stops a
    compromise climbing back out of it. Both lines look like boilerplate and
    delete cleanly, and nothing about the running stack would look different
    afterwards."""
    service = _gateway_service()

    assert "no-new-privileges:true" in service, (
        "gateway does not set no-new-privileges, so a setuid binary inside the"
        " container could raise privileges"
    )
    assert re.search(r"cap_drop:\s*\n\s*-\s*ALL", service), (
        "gateway does not drop capabilities — it needs none of them, and"
        " keeping them costs nothing to remove"
    )
