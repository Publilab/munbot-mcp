import json
import re
import unicodedata
from pathlib import Path
from typing import Dict, List, Tuple, Optional
try:  # normal package import
    from ..classification_utils import ASPECT_PRIORITY
except Exception:  # pragma: no cover - fallback when importing as top-level 'utils.kb'
    try:
        from classification_utils import ASPECT_PRIORITY  # type: ignore
    except Exception:
        # Fallback local si el import falla: favorecer requisitos por sobre donde
        ASPECT_PRIORITY = ["requisitos", "costos", "horarios", "donde", "plazos", "proposito"]

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None  # Lazy import in loader


def normalize(text: str) -> str:
    """Lowercase, strip accents, remove punctuation and collapse spaces."""
    if not isinstance(text, str):
        return ""
    s = text.strip().lower()
    # Remove accents
    s = (
        unicodedata.normalize("NFKD", s)
        .encode("ascii", "ignore")
        .decode("ascii")
    )
    # Replace non-alphanumeric with space
    s = re.sub(r"[^a-z0-9]+", " ", s)
    # Collapse spaces
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _repo_root() -> Path:
    # mcp-core/utils/kb.py -> parent is utils, then mcp-core, then repo root
    return Path(__file__).resolve().parents[1].parent


def load_kb() -> Tuple[Dict[str, dict], Dict[str, str], Dict[str, List[str]], Dict[str, List[str]]]:
    """
    Load deterministic KB files from repo-level kb/ and build indexes.

    Returns a tuple: (by_id, by_alias, aspect_map, categorias)
      - by_id:        tramite_id -> tramite dict
      - by_alias:     normalized alias -> tramite_id
      - aspect_map:   aspect -> list of normalized phrases
      - categorias:   category_name -> list of tramite_ids
    """
    root = _repo_root()
    kb_dir = root / "kb"

    # Load tramites.json
    tramites_path = kb_dir / "tramites.json"
    data = json.loads(tramites_path.read_text(encoding="utf-8"))
    tramites: List[dict] = data.get("tramites", [])

    by_id: Dict[str, dict] = {}
    by_alias: Dict[str, str] = {}
    for t in tramites:
        tid = t.get("id")
        if not tid:
            continue
        by_id[tid] = t
        aliases = list(t.get("aliases") or [])
        # Include the id as a searchable alias (with underscores as spaces)
        aliases.append(tid.replace("_", " "))
        for alias in aliases:
            key = normalize(alias)
            if key:
                by_alias[key] = tid

    # Load aspect map (YAML)
    aspect_path = kb_dir / "aspect_map.yml"
    global yaml  # ensure ref from outer scope
    if yaml is None:  # pragma: no cover
        import importlib

        yaml = importlib.import_module("yaml")
    aspect_raw = yaml.safe_load(aspect_path.read_text(encoding="utf-8")) or {}
    aspect_map: Dict[str, List[str]] = {}
    for aspect, variants in (aspect_raw or {}).items():
        aspect_map[str(aspect)] = [normalize(v) for v in (variants or [])]

    # Load categorias.json (opcional) y normalizar claves
    cat_path = kb_dir / "categorias.json"
    try:
        categorias_raw = json.loads(cat_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        categorias_raw = {}
    categorias: Dict[str, List[str]] = {}
    for k, ids in (categorias_raw or {}).items():
        categorias[normalize(k)] = list(dict.fromkeys(ids or []))

    # Merge dinámico: asegura que todos los trámites queden indexados por su 'categoria'
    for t in tramites:
        tid = t.get("id")
        cat = t.get("categoria")
        if not tid or not isinstance(cat, str) or not cat.strip():
            continue
        cat_norm = normalize(cat)
        lst = categorias.setdefault(cat_norm, [])
        if tid not in lst:
            lst.append(tid)

    return by_id, by_alias, aspect_map, categorias


def match_tramite(text: str, by_alias: Dict[str, str]) -> Optional[str]:
    """Return tramite_id if any alias is contained in the text (normalized)."""
    norm = normalize(text)
    if not norm:
        return None
    # Try longest aliases first to avoid partial overshadowing
    for alias in sorted(by_alias.keys(), key=len, reverse=True):
        if alias and alias in norm:
            return by_alias[alias]
    return None


def match_aspect(text: str, aspect_map: Dict[str, List[str]]) -> Optional[str]:
    """Return the highest-priority aspect matched in text, or None."""
    aspects = detect_aspects(text, aspect_map)
    return aspects[0] if aspects else None


def detect_aspects(text: str, aspect_map: Dict[str, List[str]]) -> List[str]:
    """Return all matched aspects in priority order.

    Priority order is defined by ASPECT_PRIORITY: donde > requisitos > costos > horarios > plazos > proposito.
    """
    norm = normalize(text)
    if not norm:
        return []
    found: List[str] = []
    for aspect, variants in aspect_map.items():
        for v in variants:
            if v and v in norm:
                found.append(aspect)
                break
    # Deduplicate while keeping first occurrence
    seen = set()
    dedup: List[str] = []
    for a in found:
        if a not in seen:
            dedup.append(a)
            seen.add(a)
    # Sort by explicit priority
    pri_index = {a: i for i, a in enumerate(ASPECT_PRIORITY)}
    dedup.sort(key=lambda x: pri_index.get(x, len(ASPECT_PRIORITY)))
    return dedup


_CATEGORY_KEYWORDS = {
    "certificados": [
        "certificado",
        "certificados",
        "certificacion",
        "certificaciones",
        "papel",
        "papeles",
        "antecedente",
        "antecedentes",
    ],
    "permisos": [
        "permiso",
        "permisos",
        "licencia",
        "licencias",
        "patente",
        "patentes",
        "autorizacion",
        "autorizaciones",
    ],
}


def match_categoria(text: str) -> Optional[str]:
    """Detect basic category from keywords in text (deterministic)."""
    norm = normalize(text)
    if not norm:
        return None
    for cat, words in _CATEGORY_KEYWORDS.items():
        for w in words:
            if f" {w} " in f" {norm} ":
                return cat
    return None


__all__ = [
    "load_kb",
    "normalize",
    "match_tramite",
    "match_aspect",
    "detect_aspects",
    "match_categoria",
]
