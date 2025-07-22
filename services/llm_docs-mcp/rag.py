from embeddings import embed
from qdrant_client import buscar_fragmentos
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
