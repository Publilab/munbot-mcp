import os
import json
import time
import redis
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

def expand_query_with_aliases(
    query: str, selected_doc: str | None, kb_items: list[dict], max_aliases: int = 8
) -> str:
    """Expande la consulta con alias basados en 'doc' y 'alias'."""
    q = query.strip()
    aliases: list[str] = []

    if selected_doc:
        for it in kb_items:
            doc_val = it.get("doc") or it.get("payload", {}).get("doc")
            if doc_val == selected_doc:
                aliases += it.get("alias") or it.get("payload", {}).get("alias") or []
        aliases = list(dict.fromkeys(a.strip() for a in aliases if a))[:max_aliases]
        if aliases:
            q = q + " " + " ".join(aliases)
        return q

    doc_counts: dict[str, int] = {}
    for it in kb_items:
        d = (it.get("doc") or it.get("payload", {}).get("doc") or "").strip()
        if not d:
            continue
        doc_counts[d] = doc_counts.get(d, 0) + 1
    if doc_counts:
        doc_top = max(doc_counts, key=doc_counts.get)
        for it in kb_items:
            if (it.get("doc") or it.get("payload", {}).get("doc")) == doc_top:
                aliases += it.get("alias") or it.get("payload", {}).get("alias") or []
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
) -> list[dict]:
    """Obtiene y reordena fragmentos de Qdrant."""
    rewritten_query = expand_query_with_aliases(consulta, tema_especifico or tramite, KNOWLEDGE_BASE)
    vec = embed([rewritten_query])[0]

    filtro_final = combine_filters(
        filter_by_document(tema_especifico),
        filter_by_procedure_id(tramite),
        filter_by_department_id(departamento),
        filter_by_domain(dominios),
    )

    with RAG_QDRANT_LATENCY.time():
        initial_results = search_in_qdrant(vec, top_k=k * 3, filtro=filtro_final)

    # Formateo de resultados para el reranking
    resultados_formatados = []
    if initial_results:
        for res in initial_results:
            if hasattr(res, 'payload') and 'texto' in res.payload:
                # Aseguramos que el score siempre esté presente
                payload = res.payload.copy()
                payload['score'] = res.score if hasattr(res, 'score') else 0.0
                resultados_formatados.append(payload)

    with RAG_RERANK_LATENCY.time():
        reranked_results = rerank_results(rewritten_query, resultados_formatados)
    
    return reranked_results[:k]

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
) -> dict:
    """
    Genera una respuesta completa ejecutando el pipeline RAG:
    1. Planifica la respuesta si la pregunta es compleja.
    2. Obtiene fragmentos relevantes de la base de conocimiento.
    3. Genera una respuesta basada en los fragmentos.
    4. Verifica que la respuesta sea atribuible al contexto (Guardrail).
    """
    # 1. Planificación (Plan-then-Generate)
    plan = crear_plan(pregunta, dominios)
    
    # 2. Obtención de fragmentos (Retrieve & Rerank)
    fragmentos = []
    for paso in plan:
        fragmentos_paso = obtener_fragmentos(
            consulta=paso, k=k, tema_especifico=tema_especifico, 
            tramite=tramite, departamento=departamento, dominios=dominios
        )
        fragmentos.extend(fragmentos_paso)

    # Eliminar duplicados y mantener el orden
    fragmentos_unicos = list({f['texto']: f for f in fragmentos}.values())

    if not fragmentos_unicos:
        return {"respuesta": "No encontré información relevante para tu consulta.", "fuentes": [], "error": None}

    # 3. Generación de Respuesta
    contexto = "\n".join(f["texto"] for f in fragmentos_unicos)
    prompt_template = load_prompt("doc-generar_respuesta_llm.txt")
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
) -> str:
    """Genera una respuesta de texto simple (sin fuentes)."""
    resultado = generar_respuesta(
        pregunta, tema_especifico=tema_especifico, tramite=tramite, 
        departamento=departamento, dominios=dominios
    )
    return resultado["respuesta"]

def doc_generar_respuesta_llm_with_sources(
    pregunta: str,
    tema_especifico: str | None = None,
    tramite: str | None = None,
    departamento: str | None = None,
    dominios: list[str] | None = None,
) -> dict:
    """Genera una respuesta completa con texto y fuentes."""
    return generar_respuesta(
        pregunta, tema_especifico=tema_especifico, tramite=tramite, 
        departamento=departamento, dominios=dominios
    )
