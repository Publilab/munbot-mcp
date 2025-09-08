import os
import json
import time
import redis
import random
from embeddings import embed
from llama_client import LlamaClient
from qdrant_utils import (
    search_in_qdrant,
    filter_by_document,
    filter_by_procedure_id,
    filter_by_department_id,
    filter_by_domain,
    combine_filters,
)
from sentence_transformers import CrossEncoder
from prometheus_client import Counter, Histogram

# --- Inicialización de Clientes y Modelos ---
lama = LlamaClient()
rerank_model = CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2', max_length=512)

# --- Configuración de Redis ---
REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)

# --- Rutas y Carga de Datos ---
DOCUMENTS_PATH = os.path.join(os.path.dirname(__file__), 'documents')
PROMPTS_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), '..', 'mcp-core', 'prompts')
)
KNOWLEDGE_BASE = []

# --- Configuración por categoría ---
RAG_CATEGORY_AWARE = os.getenv("RAG_CATEGORY_AWARE", "false").lower() == "true"
RAG_COLLECTION_FAQ = os.getenv("RAG_COLLECTION_FAQ")
RAG_COLLECTION_TRAMITES = os.getenv("RAG_COLLECTION_TRAMITES")
RAG_COLLECTION_NORMATIVA = os.getenv("RAG_COLLECTION_NORMATIVA")

PROMPT_FAQ = os.getenv("PROMPT_FAQ", "doc-generar_respuesta_llm.txt")
PROMPT_TRAMITE = os.getenv("PROMPT_TRAMITE", "doc-generar_respuesta_llm.txt")
PROMPT_DOCUMENTO = os.getenv("PROMPT_DOCUMENTO", "doc-generar_respuesta_llm.txt")

def load_knowledge_base():
    """Carga todos los archivos RAG-*.json en una base de conocimiento en memoria."""
    if not os.path.exists(DOCUMENTS_PATH):
        return
    for filename in os.listdir(DOCUMENTS_PATH):
        if filename.startswith("RAG-") and filename.endswith(".json"):
            with open(os.path.join(DOCUMENTS_PATH, filename), 'r', encoding='utf-8') as f:
                data = json.load(f)
                KNOWLEDGE_BASE.extend(data)

load_knowledge_base()

# Respuesta por defecto para smalltalk cuando falta información
DEFAULT_GREETING = "¡Hola! ¿En qué puedo ayudarte hoy?"

# --- Métricas de Prometheus ---
RAG_QDRANT_LATENCY = Histogram('rag_qdrant_latency_seconds', 'Latencia de la búsqueda en Qdrant')
RAG_RERANK_LATENCY = Histogram('rag_rerank_latency_seconds', 'Latencia del reranking')
RAG_LLM_LATENCY = Histogram('rag_llm_latency_seconds', 'Latencia de la generación de respuesta del LLM')
RAG_ATTRIBUTION_SUCCESS = Counter('rag_attribution_success_total', 'Verificaciones de atribución exitosas')
RAG_ATTRIBUTION_FAILURE = Counter('rag_attribution_failure_total', 'Verificaciones de atribución fallidas')

# --- Funciones de Ayuda ---
def load_prompt(prompt_name: str) -> str:
    """Carga un prompt desde la carpeta de prompts de mcp-core."""
    prompt_file = os.path.join(PROMPTS_PATH, prompt_name)
    with open(prompt_file, "r", encoding="utf-8") as f:
        return f.read()


def _category_config(categoria: str | None) -> tuple[str | None, str]:
    """Devuelve la colección y el prompt asociados a una categoría."""
    if not (RAG_CATEGORY_AWARE and categoria):
        return None, PROMPT_DOCUMENTO
    cat = categoria.lower()
    if cat == "faq":
        return RAG_COLLECTION_FAQ, PROMPT_FAQ
    if cat in {"tramite", "trámite", "tramites", "trámites"}:
        return RAG_COLLECTION_TRAMITES, PROMPT_TRAMITE
    return RAG_COLLECTION_NORMATIVA, PROMPT_DOCUMENTO


def build_context(item: dict) -> str:
    """Arma el contexto a entregar al LLM usando los campos reales."""
    parts: list[str] = []

    base_text = item.get("texto")
    if not base_text:
        base_text = item.get("respuesta") or item.get("answer") or ""

    if base_text:
        parts.append(base_text)

    variants = item.get("answer_variants") or []
    if variants:
        parts.append(
            "\nVariantes sugeridas (no obligatorias):\n- " + "\n- ".join(variants)
        )

    return "\n".join([p for p in parts if p])


def render_smalltalk(item: dict) -> str:
    """Devuelve una respuesta directa de smalltalk."""
    base = item.get("respuesta") or item.get("answer") or ""
    variants = item.get("answer_variants") or []

    if variants:
        pool = ([base] if base else []) + variants
        return random.choice(pool)

    return base or DEFAULT_GREETING


def build_index_blob(obj: dict) -> str:
    """Texto a enviar al índice vectorial (Qdrant/FAISS/…)."""
    campos: list[str] = []

    titulo = obj.get("title") or obj.get("pregunta") or ""
    texto = obj.get("texto") or obj.get("respuesta") or ""
    alias = obj.get("alias") or []
    tags = (obj.get("metadata") or {}).get("tags") or obj.get("tags") or []
    user_says = obj.get("user_says") or []
    ans_vars = obj.get("answer_variants") or []

    campos.extend([titulo, texto])
    campos.extend(tags)
    campos.extend(alias)
    campos.extend(user_says)
    campos.extend(ans_vars)

    return " ".join([c for c in campos if c])


def pick_faq_smalltalk_item(faq_items: list, sub_intent: str) -> dict | None:
    candidates = [
        it
        for it in faq_items
        if (it.get("metadata") or {}).get("subcategory") == sub_intent
    ]
    candidates.sort(key=lambda x: (x.get("metadata") or {}).get("priority", 99))
    return candidates[0] if candidates else None

def expand_query_with_aliases(
    query: str, selected_doc: str | None, kb_items: list[dict], max_aliases: int = 8
) -> str:
    """Expande la consulta con alias basados en 'doc' y 'alias'."""
    q = query.strip()
    aliases: list[str] = []

    if selected_doc:
        for it in kb_items:
            # Access 'doc', 'id' and 'alias' directly from the item or its payload
            # as normalize_item ensures their presence when available.
            current_doc = it.get("doc") or it.get("payload", {}).get("doc")
            current_id = (
                it.get("id")
                or it.get("metadata", {}).get("id")
                or it.get("payload", {}).get("metadata", {}).get("id")
            )
            if current_doc == selected_doc or current_id == selected_doc:
                item_aliases = it.get("alias") or it.get("payload", {}).get("alias") or []
                aliases.extend(item_aliases)
        aliases = list(dict.fromkeys(a.strip() for a in aliases if a))[:max_aliases]
        if aliases:
            q = q + " " + " ".join(aliases)
        return q

    doc_counts: dict[str, int] = {}
    for it in kb_items:
        # Access 'doc' directly from the item or its payload
        d = (it.get("doc") or it.get("payload", {}).get("doc") or "").strip()
        if not d:
            continue
        doc_counts[d] = doc_counts.get(d, 0) + 1
    if doc_counts:
        doc_top = max(doc_counts, key=doc_counts.get)
        for it in kb_items:
            # Access 'doc' and 'alias' directly from the item or its payload
            current_doc = it.get("doc") or it.get("payload", {}).get("doc")
            if current_doc == doc_top:
                item_aliases = it.get("alias") or it.get("payload", {}).get("alias") or []
                aliases.extend(item_aliases)
        aliases = list(dict.fromkeys(a.strip() for a in aliases if a))[:max_aliases]
        if aliases:
            q = q + " " + " ".join(aliases)
    return q

def rerank_results(query: str, results: list[dict]) -> list[dict]:
    """Reordena los resultados de la búsqueda usando un CrossEncoder."""
    if not results:
        return []
    pairs = [(query, r['texto']) for r in results]
    scores = rerank_model.predict(pairs)
    for r, score in zip(results, scores):
        r['rerank_score'] = score
    return sorted(results, key=lambda x: x['rerank_score'], reverse=True)

# --- Pipeline Principal de RAG ---
def crear_plan(pregunta: str, dominios: list[str] | None = None) -> list[str]:
    """Crea un plan de respuesta para una pregunta compleja."""
    prompt_template = load_prompt("doc-create_plan.txt")
    prompt = prompt_template.replace("{{pregunta}}", pregunta)
    if dominios:
        prompt += f"\nDominios a considerar: {(', '.join(dominios))}"
    respuesta_texto = lama.generate(prompt)
    try:
        respuesta_json = json.loads(respuesta_texto)
        return respuesta_json.get("plan", [])
    except (json.JSONDecodeError, TypeError):
        return [pregunta] # Fallback a la pregunta original

def obtener_fragmentos(
    consulta: str,
    k: int = 3,
    tema_especifico: str | None = None,
    tramite: str | None = None,
    departamento: str | None = None,
    dominios: list[str] | None = None,
    collection_name: str | None = None,
):
    vec = embed([consulta])[0]

    # Construir filtros basados en hints
    filtro_doc = filter_by_document(tema_especifico)
    filtro_tramite = filter_by_procedure_id(tramite)
    filtro_depto = filter_by_department_id(departamento)
    filtro_dom = filter_by_domain(dominios)

    # Combinar filtros
    filtro_final = combine_filters(filtro_doc, filtro_tramite, filtro_depto, filtro_dom)

    # 1) Intentamos búsqueda con filtros y top_k solicitado
    hits = search_in_qdrant(vec, top_k=k, filtro=filtro_final, collection_name=collection_name)

    # 2) Fallback: si no hay hits, reintentar sin filtros y con más resultados
    if not hits:
        # log opcional
        try:
            import logging
            logging.getLogger("rag").info("No hits with filters, retrying without filters")
        except Exception:
            pass

        # Reintento sin filtros conservador (más candidatos)
        hits = search_in_qdrant(vec, top_k=max(k, 10), filtro=None, collection_name=collection_name)

    resultados = []
    for h in hits:
        payload = getattr(h, "payload", {}) or {}
        resultados.append(
            {
                "doc": payload.get("doc") or payload.get("fuente", ""),
                "titulo": payload.get("titulo") or payload.get("doc") or "",
                "texto": payload.get("texto") or payload.get("text") or "",
                "puntaje": getattr(h, "score", 0.0),
            }
        )
    return resultados

def verificar_atribucion(respuesta: str, contexto: str) -> bool:
    """Verifica si la respuesta del LLM se puede atribuir al contexto proporcionado."""
    prompt = f"Contexto: {contexto}\n\nRespuesta: {respuesta}\n\n¿Se puede responder la pregunta basándose únicamente en el contexto? Responde solo SÍ o NO."
    verificacion = lama.generate(prompt).strip().upper()
    es_atribuible = "SÍ" in verificacion
    
    (RAG_ATTRIBUTION_SUCCESS if es_atribuible else RAG_ATTRIBUTION_FAILURE).inc()
    return es_atribuible

def generar_respuesta(
    pregunta: str,
    k: int = 3,
    tema_especifico: str | None = None,
    tramite: str | None = None,
    departamento: str | None = None,
    dominios: list[str] | None = None,
    categoria: str | None = None,
) -> dict:
    """
    Genera una respuesta completa ejecutando el pipeline RAG:
    1. Planifica la respuesta si la pregunta es compleja.
    2. Obtiene fragmentos relevantes de la base de conocimiento.
    3. Genera una respuesta basada en los fragmentos.
    4. Verifica que la respuesta sea atribuible al contexto (Guardrail).
    """
    collection_name, prompt_name = _category_config(categoria)

    # 1. Planificación (Plan-then-Generate)
    plan = crear_plan(pregunta, dominios)

    # 2. Obtención de fragmentos (Retrieve & Rerank)
    fragmentos = []
    for paso in plan:
        fragmentos_paso = obtener_fragmentos(
            consulta=paso,
            k=k,
            tema_especifico=tema_especifico,
            tramite=tramite,
            departamento=departamento,
            dominios=dominios,
            collection_name=collection_name,
        )
        fragmentos.extend(fragmentos_paso)

    # Eliminar duplicados y mantener el orden
    fragmentos_unicos = list({f['texto']: f for f in fragmentos}.values())

    if not fragmentos_unicos:
        return {"respuesta": "No encontré información relevante para tu consulta.", "fuentes": [], "error": None}

    # 3. Generación de Respuesta
    contexto = "\n".join(f["texto"] for f in fragmentos_unicos)
    prompt_template = load_prompt(prompt_name)
    prompt = prompt_template.replace("{{contexto}}", contexto).replace("{{pregunta}}", pregunta)
    
    with RAG_LLM_LATENCY.time():
        respuesta_texto = lama.generate(prompt)
    
    # 4. Verificación de Atribución (Guardrail)
    if verificar_atribucion(respuesta_texto, contexto):
        return {"respuesta": respuesta_texto, "fuentes": fragmentos_unicos, "error": None}
    else:
        # Si la atribución falla, se devuelve una respuesta genérica con las fuentes.
        return {
            "respuesta": "Encontré información que podría ser relevante, pero no pude construir una respuesta directa. Te sugiero revisar las fuentes.",
            "fuentes": fragmentos_unicos,
            "error": "attribution_failed"
        }

# === API utilizada por los microservicios ===
def doc_generar_respuesta_llm(
    pregunta: str,
    tema_especifico: str | None = None,
    tramite: str | None = None,
    departamento: str | None = None,
    dominios: list[str] | None = None,
    categoria: str | None = None,
) -> str:
    """Genera una respuesta de texto simple (sin fuentes)."""
    resultado = generar_respuesta(
        pregunta,
        tema_especifico=tema_especifico,
        tramite=tramite,
        departamento=departamento,
        dominios=dominios,
        categoria=categoria,
    )
    return resultado["respuesta"]

def doc_generar_respuesta_llm_with_sources(
    pregunta: str,
    tema_especifico: str | None = None,
    tramite: str | None = None,
    departamento: str | None = None,
    dominios: list[str] | None = None,
    categoria: str | None = None,
) -> dict:
    """Genera una respuesta completa con texto y fuentes."""
    return generar_respuesta(
        pregunta,
        tema_especifico=tema_especifico,
        tramite=tramite,
        departamento=departamento,
        dominios=dominios,
        categoria=categoria,
    )


def doc_buscar_fragmento_documento(
    texto: str,
    documento: str | None = None,
    top_k: int = 3,
    score_threshold: float | None = None,
) -> list[dict]:
    """Busca fragmentos dentro de un documento específico.

    Parameters
    ----------
    texto : str
        Texto o consulta a buscar.
    documento : str | None
        Documento sobre el cual limitar la búsqueda.
    top_k : int
        Número máximo de fragmentos a devolver.
    score_threshold : float | None
        Umbral mínimo para ``rerank_score``. Si es ``None`` no se filtran los resultados.

    Returns
    -------
    list[dict]
        Fragmentos que cumplen con los criterios de búsqueda.
    """
    resultados = obtener_fragmentos(
        consulta=texto,
        k=top_k,
        tema_especifico=documento,
    )
    if score_threshold is not None:
        resultados = [r for r in resultados if r.get("rerank_score", 0) >= score_threshold]
    return resultados
