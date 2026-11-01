#!/usr/bin/env python3
"""Fail the build when inline browser JavaScript does not parse."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> int:
    source = Path("app/static/index.html").read_text(encoding="utf-8")
    scripts = re.findall(r"<script(?:\s[^>]*)?>(.*?)</script>", source, flags=re.DOTALL | re.IGNORECASE)
    if not scripts:
        print("No inline script found", file=sys.stderr)
        return 1
    node = shutil.which("node")
    if node is None:
        print("node is required to validate the demo JavaScript", file=sys.stderr)
        return 1
    with tempfile.NamedTemporaryFile("w", suffix=".js", encoding="utf-8") as handle:
        handle.write("\n".join(scripts))
        handle.flush()
        completed = subprocess.run([node, "--check", handle.name], check=False)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
