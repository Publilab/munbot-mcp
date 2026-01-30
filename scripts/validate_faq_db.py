#!/usr/bin/env python3
import json
from pathlib import Path


REQUIRED_FIELDS = [
    "id_faq",
    "pregunta_canonica",
    "variantes",
    "respuesta_oficial",
    "fuente",
    "tags",
    "vigencia",
    "ultima_revision",
    "owner_aprobador",
]


def main():
    path = Path("docs/faq_db.json")
    if not path.exists():
        print("Missing docs/faq_db.json (use docs/faq_db_template.md to build it)")
        return
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise SystemExit("FAQ DB must be a list of entries")
    errors = []
    for idx, entry in enumerate(data):
        if not isinstance(entry, dict):
            errors.append(f"entry {idx} not an object")
            continue
        for f in REQUIRED_FIELDS:
            if f not in entry:
                errors.append(f"entry {idx} missing {f}")
    if errors:
        print("FAQ DB validation failed:")
        for e in errors:
            print("-", e)
        raise SystemExit(2)
    print(f"FAQ DB validation OK ({len(data)} entries).")


if __name__ == "__main__":
    main()
