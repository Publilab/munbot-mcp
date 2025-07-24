from embeddings import embed
from qdrant_helpers import buscar_fragmentos
from llama_runner import LlamaRunner


llama = LlamaRunner()


def obtener_fragmentos(consulta: str, k: int = 3, documento: str | None = None):
    vec = embed([consulta])[0]
    hits = buscar_fragmentos(vec, top_k=k, filtro_doc=documento)
    resultados = []
    for h in hits:
        payload = getattr(h, "payload", {}) or {}
        resultados.append({
            "doc_id": payload.get("doc") or payload.get("fuente", ""),
            "titulo": payload.get("titulo") or payload.get("doc") or "",
            "parrafo": payload.get("texto") or payload.get("text") or "",
            "puntaje": getattr(h, "score", 0.0),
        })
    return resultados


def generar_respuesta(pregunta: str, k: int = 3, documento: str | None = None):
    fragmentos = obtener_fragmentos(pregunta, k, documento)
    contexto = "\n".join(f["parrafo"] for f in fragmentos)
    prompt = f"{contexto}\n\nPregunta: {pregunta}\nRespuesta:"
    respuesta = llama.generate(prompt)
    return {"respuesta": respuesta, "fragmentos": fragmentos}


# === API simplificada utilizada por algunos servicios ===
def doc_buscar_fragmento_documento(pregunta: str, documento: str | None = None):
    """Devuelve una lista de fragmentos de texto para una pregunta dada."""
    resultados = obtener_fragmentos(pregunta, 5, documento)
    return [r["parrafo"] for r in resultados]


def construir_prompt_con_fragmentos(pregunta: str, fragmentos: list[str]) -> str:
    joined = "\n".join([f"- {frag}" for frag in fragmentos])
    return (
        "Responde a la siguiente pregunta usando la información dada.\n\n"
        f"Pregunta: {pregunta}\n\n"
        "Información relevante:\n"
        f"{joined}\n\nRespuesta:"
    )


def doc_generar_respuesta_llm(pregunta: str, documento: str | None = None) -> str:
    fragmentos = doc_buscar_fragmento_documento(pregunta, documento)
    prompt = construir_prompt_con_fragmentos(pregunta, fragmentos)
    respuesta = llama.generate(prompt)
    return respuesta
