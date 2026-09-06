"""Every image in the dev stack names a specific version.

A floating tag means `docker compose pull` can change what the stack is
without any commit saying so. Two of these matter more than the rest: Redis
holds the lifetime spend ledger and the rate-limit buckets, and Postgres holds
the audit log.

The rule is that the version part of the tag — everything before the first
`-`, so `7.4.10` in `7.4.10-alpine` — must contain a dot. That rejects
`latest` and bare majors like `7-alpine` or `16-alpine`, while accepting the
different shapes upstreams actually use: `1.57`, `v3.14.0`, `16.14-alpine`.
"""

import pathlib
import re

COMPOSE = (pathlib.Path(__file__).resolve().parent.parent / "docker-compose.yml").read_text()


def _images() -> list[str]:
    return re.findall(r"^\s*image:\s*(\S+)\s*$", COMPOSE, re.M)


def test_every_image_is_pinned_to_a_version():
    images = _images()
    assert images, "no images found in docker-compose.yml — the guard would pass vacuously"

    floating = []
    for image in images:
        if "@sha256:" in image:
            continue  # digest-pinned is stricter than any tag
        _, _, tag = image.rpartition(":")
        if "/" in tag or not tag:
            floating.append(f"{image} (no tag at all)")
            continue
        version = tag.split("-", 1)[0]
        if version == "latest" or "." not in version:
            floating.append(image)

    assert not floating, (
        f"{floating} do not name a specific version, so `docker compose pull`"
        " can change the stack with no commit recording it"
    )
