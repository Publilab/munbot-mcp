#!/usr/bin/env bash
set -euo pipefail

BASE_SUM=tests/chat/summary_baseline.json
CAN_SUM=tests/chat/summary_canary.json
BASE_RES=tests/chat/baseline_results.jsonl
CAN_RES=tests/chat/canary_results.jsonl

jq -n \
  --slurpfile bsum "$BASE_SUM" \
  --slurpfile br "$BASE_RES" \
  --slurpfile csum "$CAN_SUM" \
  --slurpfile cr "$CAN_RES" \
  '
    def p95(arr):
      (arr | sort | .[(length * 0.95 | floor)]);
    def pass_rate_pct(summary):
      if summary.pass_rate_pct then summary.pass_rate_pct
      elif summary.pass_rate then summary.pass_rate * 100
      else null end;
    {
      baseline: {
        pass_rate_pct: pass_rate_pct($bsum[0]),
        p95_ms: p95([ $br[].latency_ms ])
      },
      canary: {
        pass_rate_pct: pass_rate_pct($csum[0]),
        p95_ms: p95([ $cr[].latency_ms ])
      }
    }
  '
