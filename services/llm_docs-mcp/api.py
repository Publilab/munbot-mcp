from fastapi import APIRouter, Body
from .rag import (
    generar_respuesta,
    obtener_fragmentos,
    doc_generar_respuesta_llm,
    doc_generar_respuesta_llm_with_sources,
)
from .intent_classifier import classify_main_intent
from typing import Optional

router = APIRouter()


@router.post("/classify_intent")
async def classify_intent_endpoint(payload: dict = Body(...)):
    user_input = payload.get("user_input")
    output_mode = payload.get("output_mode")
    if not user_input:
        return {"error": "El campo 'user_input' es obligatorio."}
    return classify_main_intent(user_input, output_mode=output_mode)



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
