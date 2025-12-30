import os
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _load_yaml(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    global yaml
    if yaml is None:  # pragma: no cover
        import importlib

        yaml = importlib.import_module("yaml")
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def load_registry(path: Optional[str] = None) -> Dict[str, Any]:
    root = _repo_root()
    rel_path = path or os.getenv("APPS_REGISTRY_PATH", "apps/registry.yml")
    return _load_yaml(root / rel_path)


def load_global_intents(path: Optional[str] = None) -> Dict[str, Any]:
    root = _repo_root()
    rel_path = path or os.getenv("GLOBAL_INTENTS_PATH", "core/intents_global.yml")
    return _load_yaml(root / rel_path)


def list_apps(registry: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    registry = registry or load_registry()
    apps = registry.get("apps") or []
    return [a for a in apps if isinstance(a, dict)]


def load_app_config(app_id: str, registry: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    registry = registry or load_registry()
    root = _repo_root()
    for app in list_apps(registry):
        if app.get("id") != app_id:
            continue
        entrypoint = app.get("entrypoint")
        if not entrypoint:
            return {}
        app_cfg = _load_yaml(root / entrypoint)
        dept_entries = app_cfg.get("departments") or []
        departments: List[Dict[str, Any]] = []
        for dept in dept_entries:
            if isinstance(dept, str):
                dept_cfg = _load_yaml(root / dept)
                if dept_cfg:
                    departments.append(dept_cfg)
            elif isinstance(dept, dict):
                departments.append(dept)
        if dept_entries:
            app_cfg["departments"] = departments
        return app_cfg
    return {}


__all__ = [
    "load_registry",
    "load_global_intents",
    "list_apps",
    "load_app_config",
]
