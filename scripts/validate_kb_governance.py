#!/usr/bin/env python3
import argparse
import json
from pathlib import Path
from typing import Iterable


FILE_REQUIRED = ["version", "updated_at", "status", "approved_by"]
ENTRY_REQUIRED = [
    "entry_status",
    "entry_updated_at",
    "entry_approved_by",
    "entry_source",
    "entry_vigencia",
]
ENTRY_CONTAINERS = ("tramites", "items", "entries", "preguntas", "documentos")


def load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def validate_file(path: Path, allow_empty: bool) -> list[str]:
    errors: list[str] = []
    data = load_json(path)
    if not isinstance(data, dict):
        return [f"{path}: invalid json or not an object"]

    for key in FILE_REQUIRED:
        if key not in data:
            errors.append(f"{path}: missing file field '{key}'")
        elif not allow_empty and not str(data.get(key, "")).strip():
            errors.append(f"{path}: empty file field '{key}'")

    for container in ENTRY_CONTAINERS:
        entries = data.get(container)
        if not isinstance(entries, list):
            continue
        for idx, entry in enumerate(entries):
            if not isinstance(entry, dict):
                errors.append(f"{path}: {container}[{idx}] not an object")
                continue
            for key in ENTRY_REQUIRED:
                if key not in entry:
                    errors.append(f"{path}: {container}[{idx}] missing '{key}'")
                elif not allow_empty and not str(entry.get(key, "")).strip():
                    errors.append(f"{path}: {container}[{idx}] empty '{key}'")

    return errors


def iter_json_files(paths: Iterable[Path]) -> list[Path]:
    files: list[Path] = []
    for p in paths:
        if p.is_dir():
            files.extend(p.glob("*.json"))
        elif p.is_file() and p.suffix.lower() == ".json":
            files.append(p)
    return files


def main():
    parser = argparse.ArgumentParser(description="Validate KB governance metadata.")
    parser.add_argument("paths", nargs="+", help="Paths to JSON files or folders.")
    parser.add_argument("--allow-empty", action="store_true", help="Allow empty values.")
    args = parser.parse_args()

    files = iter_json_files([Path(p) for p in args.paths])
    if not files:
        print("No JSON files found.")
        raise SystemExit(1)

    all_errors: list[str] = []
    for path in files:
        all_errors.extend(validate_file(path, allow_empty=args.allow_empty))

    if all_errors:
        print("KB governance validation failed:")
        for err in all_errors:
            print(f"- {err}")
        raise SystemExit(2)

    print(f"KB governance validation OK ({len(files)} files).")


if __name__ == "__main__":
    main()
