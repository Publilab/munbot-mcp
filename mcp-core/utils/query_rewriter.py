from typing import Dict


def rewrite_query(q: str, ctx: Dict) -> str:
    """Reescribe consultas genéricas agregando pistas de contexto sin usar LLM."""
    q_l = q.strip().lower()
    # Normalización básica
    q_l = q_l.replace("info", "información")

    # Anclar señales de contexto disponibles
    hints: list[str] = []
    if ctx.get("selected_document"):
        hints.append(f"documento={ctx['selected_document']}")
    if ctx.get("selected_procedure_id"):
        hints.append(f"procedure_id={ctx['selected_procedure_id']}")
    if ctx.get("selected_department_id"):
        hints.append(f"department_id={ctx['selected_department_id']}")

    # Heurísticas para consultas muy genéricas
    if q_l in {
        "ayuda",
        "informacion",
        "información",
        "dime más",
        "dime mas",
        "que sabes",
        "qué sabes",
        "listado",
    }:
        q_l = "resumen de opciones disponibles y temas principales"

    return f"{q_l} {' '.join(hints)}".strip()
