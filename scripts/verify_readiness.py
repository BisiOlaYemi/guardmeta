#!/usr/bin/env python3
"""Fail CI/deployment smoke checks unless the app is ready and open-weight eligible."""

from __future__ import annotations

import json
import sys
from urllib.request import urlopen


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: verify_readiness.py BASE_URL", file=sys.stderr)
        return 2
    url = sys.argv[1].rstrip("/") + "/ready"
    with urlopen(url, timeout=30) as response:  # noqa: S310 - CLI URL is intentional
        status = json.load(response)
    print(json.dumps(status, indent=2))
    if not status.get("ready"):
        print("readiness check failed", file=sys.stderr)
        return 1
    if not status.get("eligibility", {}).get("eligible"):
        print("open-weight eligibility check failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
