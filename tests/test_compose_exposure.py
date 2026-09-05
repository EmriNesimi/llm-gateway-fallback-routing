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
