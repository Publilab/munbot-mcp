import os
import json
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

llama = LlamaClient()

# --- Carga de modelos y datos ---
rerank_model = CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2', max_length=512)
KNOWLEDGE_BASE = []
DOCUMENTS_PATH = os.path.join(os.path.dirname(__file__), 'documents')
PROMPTS_PATH = os.path.join(os.path.dirname(__file__), '..", "mcp-core", "prompts')

def load_knowledge_base():
    """Carga todos los archivos RAG-*.json en una base de conocimiento en memoria."""
    for filename in os.listdir(DOCUMENTS_PATH):
        if filename.startswith("RAG-") and filename.endswith(".json"):
            with open(os.path.join(DOCUMENTS_PATH, filename), 'r', encoding='utf-8') as f:
                data = json.load(f)
                KNOWLEDGE_BASE.extend(data)

load_knowledge_base()

def load_prompt(prompt_name: str) -> str:
    """Carga un prompt desde la carpeta de prompts de mcp-core."""
    prompt_file = os.path.join(PROMPTS_PATH, prompt_name)
    with open(prompt_file, "r", encoding="utf-8") as f:
        return f.read()

def rewrite_query_with_aliases(consulta: str, tramite: str | None = None) -> str:
    """Expande la consulta con alias si encuentra una coincidencia."""
    if not tramite:
        return consulta

    for item in KNOWLEDGE_BASE:
        if item.get('id_documento') == tramite or item.get('nombre') == tramite:
            aliases = item.get('alias', [])
            if aliases:
                return f"{consulta} {' '.join(aliases)}"
    return consulta

def rerank_results(query: str, results: list[dict]) -> list[dict]:
    """Reordena los resultados de la búsqueda usando un CrossEncoder."""
    if not results:
        return []
    pairs = [(query, r['parrafo']) for r in results]
    scores = rerank_model.predict(pairs)
    for r, score in zip(results, scores):
        r['rerank_score'] = score
    return sorted(results, key=lambda x: x['rerank_score'], reverse=True)

def crear_plan(pregunta: str, dominios: list[str] | None = None) -> list[str]:
    """Crea un plan de respuesta para una pregunta compleja."""
    prompt_template = load_prompt("doc-create_plan.txt")
    prompt = prompt_template.replace("{{pregunta}}", pregunta)
    if dominios:
        prompt += f"\nDominios a considerar: {(', '.join(dominios))}"
    respuesta_texto = llama.generate(prompt)
    try:
        respuesta_json = json.loads(respuesta_texto)
        return respuesta_json.get("plan", [])
    except (json.JSONDecodeError, TypeError):
        return []

# --- Configuración de Redis ---
REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)

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

lama = LlamaClient()

# --- Configuración de Redis ---
REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)

# --- TTLs para caché ---
TTL_CONTACTO = 60 * 60 * 24 * 7  # 1 semana
TTL_HORARIOS = 60 * 60 * 24  # 1 día
TTL_DEFAULT = 60 * 60 * 24 * 30 # 1 mes

def get_dynamic_ttl(respuesta: str) -> int:
    """Determina el TTL basado en el contenido de la respuesta."""
    respuesta_lower = respuesta.lower()
    if any(keyword in respuesta_lower for keyword in ["horario", "atención"]):
        return TTL_HORARIOS
    if any(keyword in respuesta_lower for keyword in ["contacto", "teléfono", "email", "dirección"]):
        return TTL_CONTACTO
    return TTL_DEFAULT

def cache_response(key: str, response: dict):
    """Guarda una respuesta en Redis con un TTL dinámico."""
    respuesta_str = json.dumps(response)
    ttl = get_dynamic_ttl(response.get("respuesta", ""))
    redis_client.set(key, respuesta_str, ex=ttl)

# --- Métricas de Prometheus ---
RAG_QDRANT_LATENCY = Histogram('rag_qdrant_latency_seconds', 'Latencia de la búsqueda en Qdrant')
RAG_RERANK_LATENCY = Histogram('rag_rerank_latency_seconds', 'Latencia del reranking')
RAG_LLM_LATENCY = Histogram('rag_llm_latency_seconds', 'Latencia de la generación de respuesta del LLM')
RAG_ATTRIBUTION_SUCCESS = Counter('rag_attribution_success_total', 'Verificaciones de atribución exitosas')
RAG_ATTRIBUTION_FAILURE = Counter('rag_attribution_failure_total', 'Verificaciones de atribución fallidas')
CACHE_HIT_COUNTER = Counter('rag_cache_hits_total', 'Respuestas de RAG servidas desde caché')
CACHE_MISS_COUNTER = Counter('rag_cache_miss_total', 'Respuestas de RAG no encontradas en caché')

# ... (el resto del archivo permanece igual)


# ... (el resto de las funciones de carga y reescritura permanecen igual)

def obtener_fragmentos(
    consulta: str,
    k: int = 3,
    tema_especifico: str | None = None,
    tramite: str | None = None,
    departamento: str | None = None,
    dominios: list[str] | None = None,
):
    rewritten_query = rewrite_query_with_aliases(consulta, tramite)
    vec = embed([rewritten_query])[0]

    filtro_doc = filter_by_document(tema_especifico)
    filtro_tramite = filter_by_procedure_id(tramite)
    filtro_depto = filter_by_department_id(departamento)
    filtro_dominios = filter_by_domain(dominios)
    filtro_final = combine_filters(filtro_doc, filtro_tramite, filtro_depto, filtro_dominios)

    start_time = time.time()
    initial_results = search_in_qdrant(vec, top_k=k * 3, filtro=filtro_final)
    RAG_QDRANT_LATENCY.observe(time.time() - start_time)
    
    # ... (formateo de resultados)
    
    start_time = time.time()
    reranked_results = rerank_results(rewritten_query, resultados_formatados)
    RAG_RERANK_LATENCY.observe(time.time() - start_time)
    
    return reranked_results[:k]

def verificar_atribucion(respuesta: str, contexto: str) -> bool:
    """Verifica si la respuesta del LLM se puede atribuir al contexto proporcionado."""
    prompt = f"Contexto: {contexto}\n\nRespuesta: {respuesta}\n\n¿Se puede responder la pregunta basándose únicamente en el contexto? Responde solo SÍ o NO."
    verificacion = llama.generate(prompt).strip().upper()
    es_atribuible = "SÍ" in verificacion
    if es_atribuible:
        RAG_ATTRIBUTION_SUCCESS.inc()
    else:
        RAG_ATTRIBUTION_FAILURE.inc()
    return es_atribuible

def generar_respuesta(
    pregunta: str,
    k: int = 3,
    tema_especifico: str | None = None,
    tramite: str | None = None,
    departamento: str | None = None,
    dominios: list[str] | None = None,
):
    # ... (lógica de planificación y obtención de fragmentos)

    contexto = "\n".join(f["parrafo"] for f in fragmentos)
    prompt_template = load_prompt("doc-generar_respuesta_llm.txt")
    prompt = prompt_template.replace("{{contexto}}", contexto).replace("{{pregunta}}", pregunta)
    
    start_time = time.time()
    respuesta_texto = llama.generate(prompt)
    RAG_LLM_LATENCY.observe(time.time() - start_time)
    
    # ... (resto de la lógica de generación y verificación)

# === API simplificada utilizada por algunos servicios ===
def doc_buscar_fragmento_documento(
    pregunta: str,
    tema_especifico: str | None = None,
    tramite: str | None = None,
    departamento: str | None = None,
    dominios: list[str] | None = None,
):
    """Devuelve una lista de fragmentos de texto para una pregunta dada."""
    resultados = obtener_fragmentos(
        pregunta, 5, tema_especifico, tramite, departamento, dominios
    )
    return [r["parrafo"] for r in resultados]

def construir_prompt_con_fragmentos(pregunta: str, fragmentos: list[str]) -> str:
    joined = "\n".join([f"- {frag}" for frag in fragmentos])
    return (
        "Responde a la siguiente pregunta usando la información dada.\n\n"
        f"Pregunta: {pregunta}\n\n"
        "Información relevante:\n"
        f"{joined}\n\nRespuesta:"
    )

def doc_generar_respuesta_llm(
    pregunta: str,
    tema_especifico: str | None = None,
    tramite: str | None = None,
    departamento: str | None = None,
    dominios: list[str] | None = None,
) -> str:
    fragmentos = doc_buscar_fragmento_documento(
        pregunta, tema_especifico, tramite, departamento, dominios
    )
    prompt = construir_prompt_con_fragmentos(pregunta, fragmentos)
    respuesta = llama.generate(prompt)
    return respuesta

def doc_generar_respuesta_llm_with_sources(
    pregunta: str,
    tema_especifico: str | None = None,
    tramite: str | None = None,
    departamento: str | None = None,
    dominios: list[str] | None = None,
) -> dict:
    fragmentos = obtener_fragmentos(
        pregunta, 5, tema_especifico, tramite, departamento, dominios
    )
    prompt = construir_prompt_con_fragmentos(
        pregunta, [f["parrafo"] for f in fragmentos]
    )
    respuesta = llama.generate(prompt)
    return {"respuesta": respuesta, "fuentes": fragmentos}