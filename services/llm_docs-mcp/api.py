from fastapi import APIRouter, Body
from .rag import (
    generar_respuesta,
    obtener_fragmentos,
    doc_generar_respuesta_llm,
    doc_generar_respuesta_llm_with_sources,
)
from typing import Optional

router = APIRouter()


@router.post("/generar_respuesta_llm")
async def generar_respuesta_llm_endpoint(payload: dict = Body(...)):
    pregunta = payload.get("pregunta")
    if not pregunta:
        return {"error": "El campo 'pregunta' es obligatorio."}

    return doc_generar_respuesta_llm(
        pregunta=pregunta,
        tema_especifico=payload.get("tema_especifico"),
        tramite=payload.get("tramite"),
        departamento=payload.get("departamento"),
    )


@router.post("/generar_respuesta_llm_with_sources")
async def generar_respuesta_llm_with_sources_endpoint(payload: dict = Body(...)):
    pregunta = payload.get("pregunta")
    if not pregunta:
        return {"error": "El campo 'pregunta' es obligatorio."}

    # Crear clave de caché
    cache_key = f"rag_response:{pregunta}:{payload.get('tema_especifico')}:{payload.get('tramite')}:{payload.get('departamento')}:{payload.get('dominios')}"
    
    # Intentar obtener de la caché
    cached_response = redis_client.get(cache_key)
    if cached_response:
        CACHE_HIT_COUNTER.inc()
        return json.loads(cached_response)
    
    # Si no está en caché, generar respuesta y guardarla
    CACHE_MISS_COUNTER.inc()
    response = doc_generar_respuesta_llm_with_sources(
        pregunta=pregunta,
        tema_especifico=payload.get("tema_especifico"),
        tramite=payload.get("tramite"),
        departamento=payload.get("departamento"),
        dominios=payload.get("dominios"),
    )
    cache_response(cache_key, response)
    return response


@router.post("/search")
async def semantic_search(
    query: str,
    k: int = 5,
    tema_especifico: Optional[str] = None,
    tramite: Optional[str] = None,
    departamento: Optional[str] = None,
):
    return obtener_fragmentos(
        consulta=query,
        k=k,
        tema_especifico=tema_especifico,
        tramite=tramite,
        departamento=departamento,
    )
