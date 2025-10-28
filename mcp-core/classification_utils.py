"""Classification utilities for deterministic routing.

Defines priority for aspects and helpers to sort them consistently.
"""

from typing import List

# Aspect priority: donde > requisitos > costos > horarios > plazos > proposito
ASPECT_PRIORITY: List[str] = [
    "donde",
    "requisitos",
    "costos",
    "horarios",
    "plazos",
    "proposito",
]

def sort_by_aspect_priority(aspects: List[str]) -> List[str]:
    idx = {a: i for i, a in enumerate(ASPECT_PRIORITY)}
    return sorted(aspects, key=lambda a: idx.get(a, len(ASPECT_PRIORITY)))

__all__ = ["ASPECT_PRIORITY", "sort_by_aspect_priority"]

