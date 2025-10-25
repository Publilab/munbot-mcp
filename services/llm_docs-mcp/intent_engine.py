# services/llm_docs-mcp/intent_engine.py
import json, os, re, unicodedata, glob
from dataclasses import dataclass
from typing import List, Dict, Any, Optional, Tuple

DATA_GLOB = os.getenv("RAG_DATA_GLOB", "RAG-*.json")  # mismo dir del proceso
# Umbral base configurable vía entorno; recomendación del informe: 0.15
UMBRAL_SCORE = float(os.getenv("INTENT_UMBRAL_SCORE", "0.15"))  # si baja de esto => n/a

def dynamic_threshold(query_text: str) -> float:
    """Retorna un umbral adaptativo según la longitud de la consulta.

    Consultas muy cortas reciben un umbral menor para no penalizar
    saludos o frases de dos palabras. A mayor longitud, el umbral vuelve
    al valor por defecto.
    """
    n = len(query_text.strip().split())
    if n <= 2:
        return 0.08
    if n <= 4:
        return 0.12
    return UMBRAL_SCORE

def _norm(s: str) -> str:
    if not s: return ""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    s = s.lower()
    # espacios, puntuación light
    s = re.sub(r"[^a-z0-9áéíóúñü\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def _tokenize(s: str) -> List[str]:
    return [t for t in _norm(s).split() if t]


def normalize_for_search(s: str) -> str:
    """Normalize text to improve search indexing.

    Lowercase, remove diacritics and collapse repeated whitespace.
    """
    return _norm(s)


def build_searchable_text(obj: dict) -> str:
    """Create a normalized blob of text from all relevant fields."""
    # Campos principales
    title = obj.get("title") or obj.get("question") or ""
    text = obj.get("text") or obj.get("answer") or ""

    # NUEVO: incluir variantes de respuesta
    answer_variants = obj.get("answer_variants") or []

    # Alias y frases de usuario
    alias = obj.get("alias") or []

    # Metadatos y tags
    md = obj.get("metadata") or {}
    tags = md.get("tags") or obj.get("tags") or []
    subcat = md.get("subcategory") or ""

    # Extra metadata fields to include in the searchable text
    extra_md_fields = [
        md.get("category"),
        md.get("tema"),
        md.get("tipo_fragmento"),
        md.get("seccion"),
        md.get("priority"),
        md.get("faq_id"),
    ]

    parts = [title, text, subcat]
    parts.extend(tags)
    parts.extend(alias)
    parts.extend(answer_variants)
    parts.extend(str(f) for f in extra_md_fields if f not in (None, ""))

    blob = " ".join([p for p in parts if p])
    return normalize_for_search(blob)

@dataclass
class Item:
    source: str
    doc: Optional[str]
    texto: str
    tags: List[str]
    alias: List[str]
    metadata: Dict[str, Any]
    title: Optional[str]
    score_hint: float = 0.0  # ajuste fino

    def searchable_text(self) -> str:
        # concatena campos relevantes para el fallback
        obj = {
            "title": self.title,
            "text": self.texto,
            "alias": self.alias,
            "answer_variants": _ensure_list(self.metadata.get("answer_variants")),
            "metadata": self.metadata,
            "tags": self.tags,
        }
        return build_searchable_text(obj)

def _ensure_list(v) -> List[str]:
    if not v: return []
    if isinstance(v, list): return [str(x) for x in v]
    return [str(v)]

def load_all() -> List[Item]:
    items: List[Item] = []
    for path in sorted(glob.glob(DATA_GLOB)):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        for obj in data:
            alias_list = _ensure_list(obj.get("alias"))
            user_says = _ensure_list(obj.get("user_says"))
            answer_variants = _ensure_list(obj.get("answer_variants"))
            meta = obj.get("metadata") or {}
            if answer_variants:
                meta["answer_variants"] = answer_variants

            # Títulos y textos pueden venir en español (pregunta/respuesta) o en inglés
            title_val = obj.get("pregunta") or obj.get("title") or obj.get("question") or meta.get("title")
            texto_val = obj.get("respuesta") or obj.get("answer") or obj.get("texto") or ""

            # Tags suelen estar en metadata.tags
            tags_val = _ensure_list(meta.get("tags") or obj.get("tags"))

            alias_norm = [_norm(a) for a in alias_list + user_says]
            items.append(Item(
                source = obj.get("fuente") or path,
                doc    = obj.get("doc"),
                texto  = texto_val,
                tags   = tags_val,
                alias  = alias_norm,
                metadata = meta,
                title    = title_val,
            ))
    return items

# ————— Reglas → intent
def tags_to_intent(tags: List[str], metadata: Dict[str, Any]) -> Tuple[str, Optional[str]]:
    t = { _norm(x) for x in tags }
    sub = metadata.get("subcategory")
    # elevaciones desde FAQ a agenda/reclamo
    if "faq" in t and (sub == "citas" or "iniciar_agendamiento" in t):
        return "agenda", "iniciar"
    if "faq" in t and (sub == "reclamos" or "iniciar_reclamo" in t):
        return "reclamo", "iniciar"

    if "tramite"   in t: return "tramite", sub
    if "faq"       in t: return "faq", sub
    if "documento" in t: return "documento", sub
    return "n/a", None

# ————— Palabras clave para slots / afinidad
KW_SLOT_TRAMITE = {
    "requisitos": {"req","requisito","requisitos","necesito","documentos"},
    "donde_obtener": {"donde","dónde","obtener","sacar","oficina","lugar"},
    "horario_atencion": {"horario","hora","abre","abren","cierra","cierran"},
    "utilidad": {"para que","utilidad","sirve","beneficio","beneficios"},
    "penalidad": {"multa","sancion","sanción","penalidad","castigo"},
    "tiempo_validez": {"vigencia","validez","vence","vencimiento","cuanto dura"},
}

# Subintentos típicos para FAQ
KW_FAQ = {
    "saludo": {"hola","buenos dias","buenas tardes","buenas noches","saludos","hello","hi"},
    "despedida": {"adios","hasta luego","chao","nos vemos"},
    "agradecimiento": {"gracias","muchas gracias"},
    "meta_bot": {"quien eres","que eres","tu nombre","eres un bot"},
}

# Subcategorías por DOCUMENTO (ayudas_sociales, contrib_derechos, horario_comercio, medio_ambiente)
KW_SUBCAT_BY_DOC: Dict[str, Dict[str, set]] = {
    "ayudas_sociales": {
        "beneficiarios": {"beneficiarios","quienes pueden","prioridad","adultos mayores","vulnerables","ric"},
        "bolsa_alimentos": {"bolsa de alimentos","alimentos","mercaderia"},
        "aporte_funerario": {"funerario","cementerio","defuncion","nicho"},
        "ayudas_salud_tecnicas": {"atencion medica","examenes","medicamentos","lentes","audifonos","ayudas tecnicas"},
        "aporte_pasajes": {"pasajes","traslado","cita medica","tribunales","rehabilitacion"},
        "materiales_construccion": {"materiales","construccion","mejoramiento vivienda"},
        "procedimiento_general": {"como solicitar","procedimiento","oficina de partes","plazo"},
        "causales_negacion": {"negar","rechazo","falsear","residencia"},
    },
    "contrib_derechos": {
        "aseo_domiciliario": {"aseo","basura","retiro residuos","tarifa basura"},
        "alumbrado_publico": {"alumbrado","luz publica","faroles","iluminacion"},
        "mantencion_espacios_publicos": {"plazas","parques","veredas","poda"},
        "servicios_cementeriales": {"cementerio","sepultacion","nichos"},
        "seguridad_ciudadana": {"seguridad","videovigilancia","guardias"},
        "permisos_edificacion": {"permiso de edificacion","obra mayor","obra menor","revision de proyectos"},
        "permisos_subdivision_loteo": {"subdivision","loteo","fusion de propiedades"},
        "patentes_comerciales_industriales": {"patentes","comerciales","industriales","alcoholes"},
        "permisos_circulacion": {"permiso de circulacion","vehicular","renovacion","duplicado"},
        "ocupacion_bienes_nacionales": {"quioscos","veredas","ferias","eventos","ocupacion espacio publico"},
        "permisos_sanitarios": {"sanitario","expendio de alimentos","peluqueria","centro estetica"},
        "permisos_espectaculos": {"espectaculos","eventos masivos","aforo"},
        "contribucion_mejoras": {"mejoras","pavimentacion","aceras"},
        "contribucion_bienes_raices": {"bienes raices","avaluo fiscal","tasas diferenciadas"},
        "exenciones_legales": {"exencion","exenciones","religiosas","beneficas","adultos mayores"},
        "beneficios_pronto_pago": {"pronto pago","descuento","pago anticipado"},
        "reclamos": {"reclamo","impugnar","cobro"},
        "recursos": {"recurso","reposicion","juzgado","apelacion"},
        "facturacion_cobranza": {"factura","cobranza","convenio","facilidades"},
        "intereses_reajustes": {"intereses","mora","reajuste","multas"},
        "prescripcion_obligaciones": {"prescripcion","plazo","interrupcion"},
    },
    "horario_comercio": {
        "cierre_general": {"cierre","cierran","supermercado","estado de excepcion","20:00","19:00"},
        "feria_libre": {"feria","libre","13:00","20:00","horario feria"},
        "general": {"decreto 481","mascarillas","funcionamiento locales"},
    },
    "medio_ambiente": {
        "residuos_solidos": {"residuos","basura","separar","organicos","reciclables"},
        "reciclaje_obligatorio": {"reciclaje","puntos de acopio","reciclar"},
        "reduccion_plasticos": {"plasticos","un solo uso","bombillas","vasos"},
        "proteccion_recursos_hidricos": {"rios","lagos","agua","vertidos"},
        "contaminacion_luminica": {"luminica","luz","iluminacion","cielo nocturno"},
        "contaminacion_acustica": {"ruido","decibeles","musica"},
        "energias_renovables": {"paneles","solares","eolica","renovables"},
        "proteccion_areas_verdes": {"parques","arboles nativos","corredores biologicos"},
        "compostaje_comunitario": {"compostaje","huertos","organicos"},
        "fiscalizacion_ambiental": {"fiscalizacion","inspecciones","sanciones"},
    },
}

def guess_slot(texto: str, metadata: Dict[str,Any], tags: Optional[List[str]] = None, doc_name: Optional[str] = None) -> Optional[str]:
    """Inferir slot/subcategoría en base a metadata, tags y nombre de documento.
    Usa matching normalizado para soportar tildes y variantes.
    """
    # 0) Preferir lo que venga explícito en metadata (trámites)
    slot = metadata.get("seccion") or metadata.get("tipo_fragmento")
    if slot:
        return slot

    t = _norm(texto)
    tagset = { _norm(x) for x in (tags or []) }
    doc = (doc_name or metadata.get("source_doc") or metadata.get("doc") or "").strip()

    # 1) FAQ → detectar saludo/despedida/agradecimiento/meta
    if "faq" in tagset:
        for subc, kws in KW_FAQ.items():
            if any(_norm(kw) in t for kw in kws):
                return subc

    # 2) TRÁMITE → slots clásicos
    if "tramite" in tagset:
        for subc, kws in KW_SLOT_TRAMITE.items():
            if any(_norm(kw) in t for kw in kws):
                return subc

    # 3) DOCUMENTO → subcategorías por documento (tema)
    if doc and doc in KW_SUBCAT_BY_DOC:
        for subc, kws in KW_SUBCAT_BY_DOC[doc].items():
            if any(_norm(kw) in t for kw in kws):
                return subc

    # 4) Fallback suave: reusar vocabulario de trámite si nada más aplica
    for subc, kws in KW_SLOT_TRAMITE.items():
        if any(_norm(kw) in t for kw in kws):
            return subc

    return None

# ————— Scoring
def alias_score(user: str, item: Item) -> Tuple[float, Optional[str]]:
    # mejor alias más largo que aparezca en user → más score
    best = 0.0; hit = None
    for a in item.alias:
        if not a: continue
        if a in user or user in a:
            s = min(len(a), len(user)) / (len(user)+1e-9)
            if s > best:
                best, hit = s, a
    return best, hit

def token_overlap_score(user: str, item: Item) -> float:
    u = set(_tokenize(user))
    if not u: return 0.0
    v = set(_tokenize(item.searchable_text()))
    inter = len(u & v)
    if inter == 0: return 0.0
    # jaccard suavizado
    return inter / (len(u | v) + 1e-9)

def boost_by_keywords(user: str, item: Item) -> float:
    bonus = 0.0
    t = _norm(user)
    sub = item.metadata.get("subcategory") or ""

    # bonificar si las keywords del slot aparecen (ahora considerando tags/doc)
    slot = guess_slot(user, item.metadata, tags=item.tags, doc_name=item.doc)
    if slot:
        if slot == (item.metadata.get("seccion") or item.metadata.get("tipo_fragmento")):
            bonus += 0.08
        if slot in (sub or ""):
            bonus += 0.12

    # bonificar por doc mencionado en el texto del usuario
    d = item.doc or ""
    if d and any(w in t for w in d.replace("_"," ").split()):
        bonus += 0.04

    return bonus

# ————— Clasificador principal
class IntentEngine:
    def __init__(self, items: Optional[List[Item]] = None):
        self.items = items or load_all()

    def classify(self, texto_usuario: str) -> Dict[str, Any]:
        u = _norm(texto_usuario)
        if not u:
            return {"intent":"n/a","sub_intent":None,"doc":None,"doc_id":None,"slot":None,
                    "matched_entry":None,"confidence":0.0,"needs_disambiguation":True}

        candidates: List[Tuple[float, Item, str]] = []

        # 1) alias-first
        for it in self.items:
            s, hit = alias_score(u, it)
            if s > 0:
                s += boost_by_keywords(u, it)
                candidates.append((s, it, "alias"))

        # 2) fallback por solapamiento si no hubo alias
        if not candidates:
            for it in self.items:
                s = token_overlap_score(u, it) + boost_by_keywords(u, it)
                if s > 0:
                    candidates.append((s, it, "bow"))

        if not candidates:
            return {"intent":"n/a","sub_intent":None,"doc":None,"doc_id":None,"slot":None,
                    "matched_entry":None,"confidence":0.0,"needs_disambiguation":True}

        # ordenar por score y “especificidad” (preferir alias)
        candidates.sort(key=lambda t: (t[0], 1 if t[2]=="alias" else 0), reverse=True)
        score, best, mode = candidates[0]

        # mapea a intent/sub
        intent, sub = tags_to_intent(best.tags, best.metadata)
        slot = guess_slot(texto_usuario, best.metadata, tags=best.tags, doc_name=best.doc)
        # doc & ids
        doc_id = best.metadata.get("id")
        matched = best.source

        # si score bajo, marcar n/a
        thr = dynamic_threshold(texto_usuario)
        if score < thr:
            return {
                "intent":"n/a", "sub_intent":None, "doc":None, "doc_id":None,
                "slot":None, "matched_entry":None, "confidence":float(round(score,4)),
                "needs_disambiguation": True
            }

        return {
            "intent": intent,
            "sub_intent": sub,
            "doc": best.doc,
            "doc_id": doc_id,
            "slot": slot,
            "matched_entry": matched,
            "confidence": float(round(min(1.0, score + best.score_hint), 4)),
            "needs_disambiguation": False
        }

# Singleton cómodo
_ENGINE: Optional[IntentEngine] = None
def get_engine() -> IntentEngine:
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = IntentEngine()
    return _ENGINE

def classify_intent_payload(texto: str) -> Dict[str, Any]:
    return get_engine().classify(texto)
