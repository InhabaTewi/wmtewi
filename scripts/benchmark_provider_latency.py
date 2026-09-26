import argparse
import json
import os
import statistics
from time import perf_counter

import httpx


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = round((len(ordered) - 1) * fraction)
    return ordered[index]


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark a non-streaming OpenAI-compatible chat provider")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--api-key", default=os.environ.get("INABA_BENCHMARK_API_KEY"))
    parser.add_argument("--warmup-runs", type=int, default=1)
    parser.add_argument("--runs", type=int, default=10)
    parser.add_argument("--timeout-seconds", type=float, default=180.0)
    args = parser.parse_args()
    if not args.api_key:
        parser.error("set INABA_BENCHMARK_API_KEY or pass --api-key")
    if args.warmup_runs < 0 or args.runs < 1 or args.timeout_seconds <= 0:
        parser.error("run counts and timeout must be positive")

    payload = {
        "model": args.model,
        "messages": [{"role": "user", "content": "Reply with exactly: benchmark ok"}],
        "max_tokens": 16,
        "temperature": 0,
    }
    headers = {"Authorization": f"Bearer {args.api_key}"}
    measurements: list[float] = []
    with httpx.Client(base_url=args.base_url.rstrip("/"), headers=headers, timeout=args.timeout_seconds) as client:
        for _ in range(args.warmup_runs):
            response = client.post("/chat/completions", json=payload)
            response.raise_for_status()
        for run in range(1, args.runs + 1):
            started = perf_counter()
            response = client.post("/chat/completions", json=payload)
            response.raise_for_status()
            total_ms = round((perf_counter() - started) * 1000)
            measurements.append(total_ms)
            print(json.dumps({"run": run, "total_ms": total_ms, "status": response.status_code}, sort_keys=True))

    report = {
        "runs": args.runs,
        "warmup_runs": args.warmup_runs,
        "total_ms": {
            "mean": round(statistics.mean(measurements)),
            "p50": round(percentile(measurements, 0.5)),
            "p95": round(percentile(measurements, 0.95)),
            "min": min(measurements),
            "max": max(measurements),
        },
        "connect_dns_tls_ms": None,
        "ttft_ms": None,
        "phase_note": "non-streaming endpoint exposes only end-to-end request duration",
    }
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())