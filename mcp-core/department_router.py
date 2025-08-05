"""Simple keyword-based department router."""

import json
import os
from typing import Dict, List, Optional, Any

try:
    from utils.text import normalize_text
except (ModuleNotFoundError, ImportError):
    # Fallback para pruebas locales
    def normalize_text(text: str) -> str:
        import re
        import unicodedata
        text = text.lower()
        text = ''.join(c for c in unicodedata.normalize('NFD', text) if unicodedata.category(c) != 'Mn')
        text = re.sub(r'[^\w\s]', '', text)
        return text

class DepartmentRouter:
    """
    Identifica un departamento específico dentro de una consulta
    basándose en un mapa de alias cargado desde un archivo JSON.

    Se realiza una normalización básica del texto para realizar
    comparaciones robustas independientemente de mayúsculas, tildes u
    otros caracteres especiales.
    """
    def __init__(self, departments_path: str):
        self.department_map: Dict[str, str] = {}
        if not os.path.exists(departments_path):
            raise FileNotFoundError(f"El archivo de departamentos no se encontró en: {departments_path}")
        
        with open(departments_path, 'r', encoding='utf-8') as f:
            departments: List[Dict[str, Any]] = json.load(f)

        for dept in departments:
            dept_id = dept.get("id_chunk") or dept.get("id")
            if not dept_id:
                continue
            for alias in dept.get("alias", []):
                self.department_map[normalize_text(alias)] = dept_id
        
        self.sorted_aliases = sorted(self.department_map.keys(), key=len, reverse=True)

    def get_department_id(self, user_input: str) -> Optional[str]:
        """Retorna el ID del departamento asociado al texto del usuario.

        La búsqueda se realiza comprobando si alguno de los alias
        normalizados está contenido en la consulta normalizada. Si no hay
        coincidencias se retorna ``None``.
        """
        normalized_input = normalize_text(user_input)
        for alias in self.sorted_aliases:
            if alias in normalized_input:
                return self.department_map[alias]
        return None


def get_department_id(query: str, router: DepartmentRouter) -> Optional[str]:
    """Helper para obtener el ID de departamento usando un router.

    Este envoltorio facilita pruebas de humo y mantiene el mismo tipo de
    retorno que ``DepartmentRouter.get_department_id``.
    """

    return router.get_department_id(query)


__all__ = ["DepartmentRouter", "get_department_id"]
