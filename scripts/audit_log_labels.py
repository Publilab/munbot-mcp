#!/usr/bin/env python3
import json
import sys
from collections import Counter

# Permite ejecución desde raíz del repo
try:
    from mcp_core.intent_audit import audit_label_from_log  # type: ignore
except Exception:  # pragma: no cover
    # Fallback si se ejecuta desde mcp-core como cwd
    from intent_audit import audit_label_from_log  # type: ignore

"""
Uso:
  python scripts/audit_log_labels.py logs.jsonl

Escanea cada línea (JSON) buscando campos posibles con etiquetas de intent:
  intent, intent_raw, intent_norm, mapped_intent, category, tool_selected
"""

FIELDS = [
    "intent",
    "intent_raw",
    "intent_norm",
    "mapped_intent",
    "category",
    "tool_selected",
]


def extract_label(obj):
    for k in FIELDS:
        v = obj.get(k)
        if isinstance(v, str) and v.strip():
            return v
    return None


def main(path):
    misses = Counter()
    suggestions = Counter()
    total = 0
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            label = extract_label(obj)
            if not label:
                continue
            total += 1
            res = audit_label_from_log(label, source="log_scan")
            if not res["ok"]:
                misses[label] += 1
                if res["suggestion"]:
                    suggestions[res["suggestion"]["canonical_candidate"]] += 1

    print(f"Escaneados: {total}")
    print("\nTop intents desconocidos:")
    for lbl, n in misses.most_common(20):
        print(f"  {lbl}: {n}")

    if suggestions:
        print("\nSugerencias de alta frecuencia (posibles mappings):")
        for cand, n in suggestions.most_common(10):
            print(f"  {cand}: {n}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python scripts/audit_log_labels.py logs.jsonl")
        sys.exit(1)
    main(sys.argv[1])

