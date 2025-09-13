# Chat Regression Tests

Run the baseline and canary test suites:

```bash
python tests/chat/run_tests.py --testset tests/chat/testset.jsonl --results tests/chat/baseline_results.jsonl --summary tests/chat/summary_baseline.json
python tests/chat/run_tests.py --testset tests/chat/testset.jsonl --results tests/chat/canary_results.jsonl --summary tests/chat/summary_canary.json
```

To compare the summaries and report the pass rate percentage and p95 latency for each environment, use the helper script:

```bash
./compare_summaries.sh
```

This script requires `jq` to be installed and assumes the summaries and result files live in `tests/chat/`.
