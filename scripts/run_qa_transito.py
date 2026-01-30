#!/usr/bin/env python3
import json
from pathlib import Path


def main():
    path = Path("tests/qa_transito_regression.jsonl")
    if not path.exists():
        raise SystemExit("qa_transito_regression.jsonl missing")

    total = 0
    by_type = {}
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            item = json.loads(line)
            total += 1
            typ = item.get("expected_type", "unknown")
            by_type[typ] = by_type.get(typ, 0) + 1

    print(f"QA dataset loaded: {total} cases")
    for k, v in sorted(by_type.items()):
        print(f"- {k}: {v}")
    print("\nNOTE: Execute runtime QA manually (chat or harness) before release.")


if __name__ == "__main__":
    main()
