#!/usr/bin/env python3
import json
from pathlib import Path


REQUIRED_FIELDS = ["pregunta", "tiene_respuesta", "tipo", "fuente", "estado"]


def main():
    path = Path("docs/matriz_cobertura.json")
    if not path.exists():
        print("Missing docs/matriz_cobertura.json (use docs/matriz_cobertura_template.md to build it)")
        return
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise SystemExit("Matriz de cobertura must be a list")
    errors = []
    for idx, entry in enumerate(data):
        if not isinstance(entry, dict):
            errors.append(f"entry {idx} not an object")
            continue
        for f in REQUIRED_FIELDS:
            if f not in entry:
                errors.append(f"entry {idx} missing {f}")
    if errors:
        print("Matriz de cobertura validation failed:")
        for e in errors:
            print("-", e)
        raise SystemExit(2)
    print(f"Matriz de cobertura OK ({len(data)} entries).")


if __name__ == "__main__":
    main()
