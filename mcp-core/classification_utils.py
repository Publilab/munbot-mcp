"""Classification utilities for deterministic routing.

Defines priority for aspects and helpers to sort them consistently.
"""

from typing import List

# Aspect priority ajustada para favorecer preguntas de contenido.
# Prioriza "requisitos" por sobre "donde" para evitar que frases como
# "¿Qué documentos debo tener para sacar…?" caigan en ubicación por la palabra "sacar".
ASPECT_PRIORITY: List[str] = [
    "requisitos",
    "costos",
    "horarios",
    "donde",
    "plazos",
    "proposito",
]

def sort_by_aspect_priority(aspects: List[str]) -> List[str]:
    idx = {a: i for i, a in enumerate(ASPECT_PRIORITY)}
    return sorted(aspects, key=lambda a: idx.get(a, len(ASPECT_PRIORITY)))

__all__ = ["ASPECT_PRIORITY", "sort_by_aspect_priority"]
