import argparse
import os
import statistics
import time

import httpx


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark the loopback OpenAI-compatible local endpoint")
    parser.add_argument("--base-url", default="http://127.0.0.1:18081/v1")
    parser.add_argument("--model", default="inaba-local-qwen")
    parser.add_argument("--api-key", default=os.environ.get("INABA_LOCAL_LLM_API_KEY"))
    parser.add_argument("--warmup-runs", type=int, default=1)
    parser.add_argument("--runs", type=int, default=10)
    args = parser.parse_args()
    if not args.api_key:
        parser.error("set INABA_LOCAL_LLM_API_KEY or pass --api-key")
    if args.warmup_runs < 0 or args.runs < 1:
        parser.error("--warmup-runs must be non-negative and --runs must be positive")

    payload = {
        "model": args.model,
        "messages": [{"role": "user", "content": "用一句中文说明你正在本地运行。"}],
        "chat_template_kwargs": {"enable_thinking": False},
        "max_tokens": 128,
        "temperature": 0,
    }
    headers = {"Authorization": f"Bearer {args.api_key}"}
    with httpx.Client(base_url=args.base_url.rstrip("/"), headers=headers, timeout=120) as client:
        for _ in range(args.warmup_runs):
            response = client.post("/chat/completions", json=payload)
            response.raise_for_status()
        measurements = []
        for run in range(1, args.runs + 1):
            started = time.perf_counter()
            response = client.post("/chat/completions", json=payload)
            response.raise_for_status()
            elapsed_seconds = time.perf_counter() - started
            completion_tokens = response.json().get("usage", {}).get("completion_tokens")
            if not isinstance(completion_tokens, int) or completion_tokens <= 0:
                raise RuntimeError("Local endpoint did not report positive completion token usage")
            tokens_per_second = completion_tokens / elapsed_seconds
            measurements.append((elapsed_seconds, completion_tokens, tokens_per_second))
            print(
                f"run={run} total_seconds={elapsed_seconds:.3f} completion_tokens={completion_tokens} "
                f"tokens_per_second={tokens_per_second:.2f} ttft_seconds=not_measured_non_streaming"
            )
    durations = [measurement[0] for measurement in measurements]
    throughputs = [measurement[2] for measurement in measurements]
    print(
        f"runs={args.runs} warmup_runs={args.warmup_runs} mean_total_seconds={statistics.mean(durations):.3f} min_total_seconds={min(durations):.3f} "
        f"mean_tokens_per_second={statistics.mean(throughputs):.2f} ttft=not_measured_non_streaming"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())