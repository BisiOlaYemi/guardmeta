#!/usr/bin/env python3
"""Evaluate the live proof-carrying treasury agent against its fixed scenarios."""

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


def request(url: str, *, method: str = "GET", payload: dict[str, Any] | None = None) -> dict:
    headers = {"Content-Type": "application/json"}
    if key := os.getenv("GUARDMETA_API_KEY", "").strip():
        headers["X-API-Key"] = key
    body = json.dumps(payload).encode() if payload is not None else None
    try:
        with urlopen(Request(url, data=body, headers=headers, method=method), timeout=180) as response:  # noqa: S310
            return json.load(response)
    except HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise RuntimeError(f"{method} {url} returned {exc.code}: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"Could not reach {url}: {exc.reason}") from exc


def run(base_url: str) -> dict[str, Any]:
    base = base_url.rstrip("/")
    readiness = request(f"{base}/ready")
    catalog = request(f"{base}/v1/treasury/scenarios")["scenarios"]
    results: list[dict[str, Any]] = []
    for scenario in catalog:
        started = time.perf_counter()
        expected = scenario["expected_outcome"]
        try:
            response = request(f"{base}/v1/treasury/run", method="POST", payload={"scenario_id": scenario["id"]})
            certificate = response["certificate"]
            actual = response["status"]
            expected_proof = "SAT" if expected == "approved" else "UNSAT"
            item = {
                "scenario_id": scenario["id"], "expected": expected, "actual": actual,
                "proof": certificate["status"],
                "passed": actual == expected and certificate["status"] == expected_proof,
                "model_calls": response["model_calls"], "tool_calls": response["tool_calls"],
                "total_tokens": response.get("usage", {}).get("total_tokens", 0),
                "policy_checks": len(certificate["checks"]),
                "counterexamples": certificate["counterexamples"],
                "certificate_sha256": certificate["certificate_sha256"],
                "request_id": response.get("request_id"),
            }
        except RuntimeError as exc:
            item = {"scenario_id": scenario["id"], "expected": expected, "actual": "error", "proof": "error",
                    "passed": False, "model_calls": 0, "tool_calls": 0, "total_tokens": 0, "error": str(exc)}
        item["latency_ms"] = round((time.perf_counter() - started) * 1000, 1)
        results.append(item)
    latencies = [item["latency_ms"] for item in results]
    passed = sum(bool(item["passed"]) for item in results)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(), "target": base,
        "eligibility": readiness.get("eligibility", {}),
        "summary": {"cases": len(results), "passed": passed,
                    "pass_rate": round(passed / len(results), 4) if results else 0,
                    "model_calls": sum(item["model_calls"] for item in results),
                    "tool_calls": sum(item["tool_calls"] for item in results),
                    "total_tokens": sum(item["total_tokens"] for item in results),
                    "median_latency_ms": round(statistics.median(latencies), 1) if latencies else 0,
                    "max_latency_ms": max(latencies, default=0)},
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
    return 0 if report["summary"]["pass_rate"] == 1 and report["eligibility"].get("eligible") else 1


if __name__ == "__main__":
    raise SystemExit(main())
