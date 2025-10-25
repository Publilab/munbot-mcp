import json
import logging
import os
import unicodedata
from difflib import get_close_matches

try:
    from prometheus_client import Counter  # type: ignore
except Exception:  # pragma: no cover
    Counter = None  # opcional si no usas Prometheus aquí


LOGGER = logging.getLogger("munbot")

# Métricas (opcional)
INTENT_UNKNOWN_TOTAL = (
    Counter("intent_unknown_total", "Intent no encontrado en registro", ["source"]) if Counter else None
)
INTENT_ALIAS_MISS_TOTAL = (
    Counter("intent_alias_miss_total", "Alias no mapea a canonico", ["source"]) if Counter else None
)

_REGISTRY_CACHE: dict | None = None


def _norm(s: str) -> str:
    if not s:
        return ""
    s = s.strip().lower()
    s = unicodedata.normalize("NFD", s)
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")  # quita tildes
    return "".join(ch for ch in s if ch.isalnum() or ch in ("_", "-", " ")).strip()


def load_registry(path: str | None = None) -> dict:
    global _REGISTRY_CACHE
    if _REGISTRY_CACHE:
        return _REGISTRY_CACHE
    path = path or os.getenv("INTENTS_REGISTRY_PATH", "mcp-core/config/intents_registry.json")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    # Normaliza todo
    canonical = sorted({_norm(x) for x in data.get("canonical", [])})
    aliases = { _norm(k): sorted({_norm(v) for v in vs}) for k, vs in data.get("aliases", {}).items() }
    _REGISTRY_CACHE = {"canonical": canonical, "aliases": aliases}
    return _REGISTRY_CACHE


def suggest_closest(label: str, choices: list[str]):
    m = get_close_matches(label, choices, n=1, cutoff=0.6)
    if not m:
        return None
    from difflib import SequenceMatcher
    score = SequenceMatcher(None, label, m[0]).ratio()
    return (m[0], round(score, 3))


def audit_intent(intent_raw: str, source: str, extra: dict | None = None) -> dict:
    """
    Verifica que intent_raw esté en el registro:
      - Si coincide con un canónico => ok
      - Si coincide con un alias => ok + sugiere canónico
      - Si no coincide => log de 'intent_registry_miss'
    """
    reg = load_registry()
    raw = intent_raw or ""
    intent_norm = _norm(raw)

    canonical = reg["canonical"]
    aliases = reg["aliases"]

    result = {
        "ok": True,
        "intent_raw": raw,
        "intent_norm": intent_norm,
        "match_type": "canonical|alias|miss",
        "canonical_intent": None,
        "alias_of": None,
        "suggestion": None,
    }

    # 1) ¿Es canónico?
    if intent_norm in canonical:
        result["match_type"] = "canonical"
        result["canonical_intent"] = intent_norm
        return result

    # 2) ¿Es alias de alguno?
    for canon, alias_list in aliases.items():
        if intent_norm in alias_list:
            result["match_type"] = "alias"
            result["canonical_intent"] = canon
            result["alias_of"] = canon
            return result

    # 3) Miss → log + métrica + sugerencias
    result["ok"] = False
    result["match_type"] = "miss"
    # Sugerir canónico más cercano
    suggestion = suggest_closest(intent_norm, canonical)
    if suggestion:
        result["suggestion"] = {"canonical_candidate": suggestion[0], "similarity": suggestion[1]}

    payload = {
        "event": "intent_registry_miss",
        "source": source,  # "classifier" | "agent" | "flow" | "log_scan"
        "intent_raw": raw,
        "intent_norm": intent_norm,
        "closest": result["suggestion"],
        "known_canonical": canonical,
        "notes": "Intent no encontrado en registry",
    }
    if extra:
        payload.update({"extra": extra})

    # Log estructurado (JSON)
    try:
        LOGGER.warning(json.dumps(payload, ensure_ascii=False))
    except Exception:  # pragma: no cover
        LOGGER.warning(payload)

    # Métricas
    if INTENT_UNKNOWN_TOTAL:
        INTENT_UNKNOWN_TOTAL.labels(source=source).inc()

    return result


def audit_label_from_log(label_raw: str, source: str = "log") -> dict:
    """
    Útil cuando ‘etiquetas en logs’ no matchean el registro.
    """
    return audit_intent(label_raw, source=source)


def audit_alias_match(term_raw: str, expected_canonical: str, source: str = "classifier") -> None:
    """Registra cuando un término no está listado como alias del canónico esperado."""
    reg = load_registry()
    term = _norm(term_raw)
    aliases = reg["aliases"].get(expected_canonical, [])
    if term and term not in aliases:
        payload = {
            "event": "intent_alias_miss",
            "source": source,
            "term_norm": term,
            "expected_canonical": expected_canonical,
            "notes": "El término no está listado como alias del canónico",
        }
        try:
            LOGGER.info(json.dumps(payload, ensure_ascii=False))
        except Exception:  # pragma: no cover
            LOGGER.info(payload)
        if INTENT_ALIAS_MISS_TOTAL:
            INTENT_ALIAS_MISS_TOTAL.labels(source=source).inc()

