import json
import os
from typing import Optional, Dict, List, Any

try:
    from utils.text import normalize_text
except (ModuleNotFoundError, ImportError):
    def normalize_text(text: str) -> str:
        import re
        import unicodedata
        text = text.lower()
        text = ''.join(c for c in unicodedata.normalize('NFD', text) if unicodedata.category(c) != 'Mn')
        text = re.sub(r'[\W_]+', ' ', text)
        return text.strip()


class DepartmentRouter:
    """Identify a department ID from user input based on alias mapping."""

    def __init__(self, departments_path: str) -> None:
        self.department_map: Dict[str, str] = {}
        if not os.path.exists(departments_path):
            raise FileNotFoundError(f"Department file not found: {departments_path}")

        with open(departments_path, "r", encoding="utf-8") as f:
            departments: List[Dict[str, Any]] = json.load(f)

        for dept in departments:
            dept_id = dept.get("id")
            if not dept_id:
                continue
            for alias in dept.get("alias", []):
                self.department_map[normalize_text(alias)] = dept_id

        # sort aliases by length (desc) to match longest first
        self.sorted_aliases = sorted(self.department_map.keys(), key=len, reverse=True)

    def get_department_id(self, text: str) -> Optional[str]:
        """Return department ID if any alias is found in text."""
        normalized = normalize_text(text)
        for alias in self.sorted_aliases:
            if alias in normalized:
                return self.department_map[alias]
        return None
