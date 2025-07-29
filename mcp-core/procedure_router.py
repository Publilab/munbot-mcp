import json
import os
from typing import Optional, Dict, List, Any

try:
    from utils.text import normalize_text
except (ModuleNotFoundError, ImportError):
    # Fallback for tests without package structure
    def normalize_text(text: str) -> str:
        import re
        import unicodedata
        text = text.lower()
        text = ''.join(
            c for c in unicodedata.normalize('NFD', text)
            if unicodedata.category(c) != 'Mn'
        )
        text = re.sub(r'[^\w\s]', '', text)
        return text


class ProcedureRouter:
    """Map user queries to a specific procedure ID based on alias matching."""

    def __init__(self, procedures_path: str):
        self.procedure_map: Dict[str, str] = {}
        if not os.path.exists(procedures_path):
            raise FileNotFoundError(f"Procedures file not found at: {procedures_path}")

        with open(procedures_path, 'r', encoding='utf-8') as f:
            procedures: List[Dict[str, Any]] = json.load(f)

        for proc in procedures:
            proc_id = proc.get("ID_Documento") or proc.get("id")
            if not proc_id:
                continue
            for alias in proc.get("alias", []):
                self.procedure_map[normalize_text(alias)] = proc_id

        self.sorted_aliases = sorted(self.procedure_map.keys(), key=len, reverse=True)

    def get_procedure_id(self, user_input: str) -> Optional[str]:
        normalized_input = normalize_text(user_input)
        for alias in self.sorted_aliases:
            if alias in normalized_input:
                return self.procedure_map[alias]
        return None
