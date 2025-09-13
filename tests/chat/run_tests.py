import argparse
import json
import statistics
import time
import urllib.request
from pathlib import Path


def run_tests(url: str, testset_path: Path, results_path: Path, summary_path: Path) -> None:
    """Run prompts against the orchestrate endpoint and store detailed results and a summary."""
    cases = []
    with testset_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            cases.append(json.loads(line))

    results = []
    with results_path.open("w", encoding="utf-8") as rf:
        for case in cases:
            prompt = case.get("prompt", "")
            expected_kind = case.get("expected_kind")
            must_contain = case.get("must_contain", "")
            max_latency_ms = case.get("max_latency_ms", 0)

            data = json.dumps({"pregunta": prompt}).encode("utf-8")
            request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")

            start = time.perf_counter()
            try:
                with urllib.request.urlopen(request, timeout=max_latency_ms / 1000 if max_latency_ms else None) as resp:
                    payload = resp.read()
                    response = json.loads(payload)
            except Exception as exc:  # pragma: no cover - network errors
                response = {"error": str(exc)}
            latency_ms = (time.perf_counter() - start) * 1000

            text = response.get("respuesta", "")
            kind = response.get("kind")
            passed = (
                kind == expected_kind
                and must_contain.lower() in text.lower()
                and (latency_ms <= max_latency_ms if max_latency_ms else True)
            )
            result = {
                "prompt": prompt,
                "expected_kind": expected_kind,
                "actual_kind": kind,
                "must_contain": must_contain,
                "response": text,
                "latency_ms": round(latency_ms, 2),
                "passed": passed,
            }
            results.append(result)
            rf.write(json.dumps(result, ensure_ascii=False) + "\n")

    latencies = [r["latency_ms"] for r in results]
    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    summary = {
        "total": total,
        "passed": passed,
        "pass_rate": passed / total if total else 0.0,
        "avg_latency_ms": statistics.mean(latencies) if latencies else 0.0,
    }
    with summary_path.open("w", encoding="utf-8") as sf:
        json.dump(summary, sf, ensure_ascii=False, indent=2)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run chat regression tests")
    parser.add_argument("--url", default="http://localhost:5000/orchestrate", help="Orchestrate endpoint URL")
    parser.add_argument("--testset", default="testset.jsonl", help="Path to test set JSONL")
    parser.add_argument("--results", required=True, help="Where to store JSONL results")
    parser.add_argument("--summary", required=True, help="Where to store summary JSON")
    args = parser.parse_args()

    run_tests(args.url, Path(args.testset), Path(args.results), Path(args.summary))


if __name__ == "__main__":
    main()
