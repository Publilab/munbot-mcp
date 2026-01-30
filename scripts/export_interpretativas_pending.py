#!/usr/bin/env python3
import json
from pathlib import Path


def main():
    src = Path("databases/interpretativas/pendientes_transito.jsonl")
    dst = Path("docs/Transito/pending_review.jsonl")
    if not src.exists():
        print("No pending file found:", src)
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with src.open("r", encoding="utf-8") as fin, dst.open("w", encoding="utf-8") as fout:
        for line in fin:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except Exception:
                continue
            fout.write(json.dumps(record, ensure_ascii=False) + "\n")
            count += 1
    print(f"Exported {count} records to {dst}")


if __name__ == "__main__":
    main()
