#!/usr/bin/env python3
"""Run the immutable public demo suite and write honest, reproducible E2E metrics."""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def _request(url: str, *, method: str = "GET", payload: dict[str, Any] | None = None) -> dict:
    body = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"}
    key = os.getenv("GUARDMETA_API_KEY", "").strip()
    if key:
        headers["X-API-Key"] = key
    request = Request(url, data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=180) as response:  # noqa: S310 - caller controls URL
            return json.load(response)
    except HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise RuntimeError(f"{method} {url} returned {exc.code}: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"Could not reach {url}: {exc.reason}") from exc


def run(base_url: str) -> dict[str, Any]:
    base = base_url.rstrip("/")
    readiness = _request(f"{base}/ready")
    catalog = _request(f"{base}/v1/demo/scenarios")["scenarios"]
    results: list[dict[str, Any]] = []
    for scenario in catalog:
        started = time.perf_counter()
        expected = scenario["expected_outcome"]
        try:
            response = _request(
                f"{base}/v1/demo/run",
                method="POST",
                payload={"scenario_id": scenario["id"]},
            )
            actual = response["decision"]["outcome"]
            result = {
                "scenario_id": scenario["id"],
                "expected": expected,
                "actual": actual,
                "passed": actual == expected,
                "attempts": response.get("attempts", 0),
                "model_calls": response["decision"].get("model_calls", 0),
                "total_tokens": response.get("usage", {}).get("total_tokens", 0),
                "request_id": response.get("request_id"),
            }
        except RuntimeError as exc:
            result = {
                "scenario_id": scenario["id"],
                "expected": expected,
                "actual": "error",
                "passed": False,
                "attempts": 0,
                "model_calls": 0,
                "total_tokens": 0,
                "error": str(exc),
            }
        result["latency_ms"] = round((time.perf_counter() - started) * 1000, 1)
        results.append(result)

    latencies = [item["latency_ms"] for item in results]
    passed = sum(bool(item["passed"]) for item in results)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "target": base,
        "eligibility": readiness.get("eligibility", {}),
        "summary": {
            "cases": len(results),
            "passed": passed,
            "pass_rate": round(passed / len(results), 4) if results else 0,
            "model_calls": sum(item["model_calls"] for item in results),
            "total_tokens": sum(item["total_tokens"] for item in results),
            "median_latency_ms": round(statistics.median(latencies), 1) if latencies else 0,
            "max_latency_ms": max(latencies, default=0),
        },
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8080")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        report = run(args.url)
    except RuntimeError as exc:
        print(f"evaluation failed: {exc}", file=sys.stderr)
        return 2
    rendered = json.dumps(report, indent=2)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    eligible = bool(report["eligibility"].get("eligible"))
    return 0 if report["summary"]["pass_rate"] == 1 and eligible else 1


if __name__ == "__main__":
    raise SystemExit(main())
