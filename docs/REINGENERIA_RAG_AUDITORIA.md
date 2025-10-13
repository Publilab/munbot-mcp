# Auditoría de seguimiento RAG MunBot

## 1. Resumen ejecutivo

- La recomendación crítica del informe `100.500-REINGENERÍA RAG.pdf` para invocar RAG dentro del _fallback_ del orquestador **no se ha implementado**: `handle_turn` todavía responde con el mensaje genérico cuando la acción mapeada es `"n/a"` y no llama a `handle_document_query`. 【F:mcp-core/orchestrator.py†L1198-L1221】
- El servicio `llm_docs-mcp` está devolviendo **errores 500** en el endpoint `/tools/doc-classify_intent_llm` porque `endpoint_classify_intent` asume que la salida del clasificador siempre es un diccionario. En los registros se observa el `AttributeError: 'str' object has no attribute 'get'`. 【F:docs/logs-llm_docs-mcp.txt†L132-L166】
- Los archivos de configuración JSON presentan **inconsistencias**: el `intent_registry` no declara `ask_document` como intención canónica aunque el `INTENT_MAP` y `intent_similarities` lo utilizan. Esto provoca las alertas `intent_registry_miss` observadas en los logs. 【F:mcp-core/config/intents_registry.json†L1-L19】【F:mcp-core/orchestrator.py†L456-L472】【F:docs/logs-mcp-core.txt†L721-L757】

## 2. Evidencia de fallas actuales

### 2.1 Errores en el clasificador de intención (llm_docs-mcp)
- Tres solicitudes consecutivas al endpoint `/tools/doc-classify_intent_llm` finalizaron con `500 Internal Server Error`.
- El rastro de pila confirma que `pred` es un `str` y, al intentar `pred.get("intent")`, se lanza `AttributeError`. 【F:docs/logs-llm_docs-mcp.txt†L132-L166】
- Mientras el error persiste, `mcp-core` cae en el mecanismo `intent.similarity_rescue`, forzando etiquetas como `saludo` o `ask_document` sin garantías de exactitud. 【F:docs/logs-mcp-core.txt†L721-L738】

### 2.2 Fallback sin RAG
- Aun cuando el clasificador rescata la intención como `ask_document`, la acción asignada termina siendo `"n/a"`, por lo que `handle_turn` responde con `Lo siento, no he entendido tu consulta…` sin intentar RAG. 【F:mcp-core/orchestrator.py†L1208-L1221】
- Esto contradice el plan de remediación del informe, donde se proponía consultar documentos dentro del fallback antes de rendirse. 【F:docs/100.500-REINGENERÍA RAG.md†L64-L120】

### 2.3 Alertas de `intent_registry_miss`
- Los logs registran repetidamente `intent_registry_miss` para la etiqueta `ask_document`, evidenciando que el registro canónico actual desconoce dicha intención. 【F:docs/logs-mcp-core.txt†L739-L757】

## 3. Revisión de configuraciones JSON

| Archivo | Observación |
| --- | --- |
| `mcp-core/config/router_config.json` | Define seis colecciones RAG y declara explícitamente que `RAG-doc_tramites.json` contiene el “permiso de aterrizaje”, por lo que la cobertura documental existe. 【F:mcp-core/config/router_config.json†L1-L20】 |
| `mcp-core/config/intents_registry.json` | Falta incorporar `ask_document` (y su alias) dentro de la lista `canonical`. Esto rompe la auditoría de intenciones. 【F:mcp-core/config/intents_registry.json†L1-L19】 |
| `mcp-core/config/intent_similarities.json` | Incluye entradas con `intent": "ask_document"`, pero al no estar en el registry la telemetría lo marca como desconocido. 【F:mcp-core/config/intent_similarities.json†L1-L12】 |
| `services/llm_docs-mcp/documents/*.json` | Los fragmentos para trámites contienen metadatos completos (tags, alias, id_chunk). El permiso de aterrizaje aparece en los prompts y colecciones esperadas. 【F:services/llm_docs-mcp/documents/RAG-doc_tramites.json†L1-L120】【F:mcp-core/prompts/classify-main_intent.txt†L15-L26】 |

## 4. Plan de acción priorizado

1. **Estabilizar el endpoint `/tools/doc-classify_intent_llm`:**
   - En `gateway.endpoint_classify_intent`, aceptar cadenas JSON o textos planos: intentar `json.loads` cuando `pred` sea `str` y forzar la conversión a diccionario antes de acceder a `.get()`.
   - Añadir pruebas unitarias en `services/llm_docs-mcp/test_tools.py` cubriendo respuestas del clasificador en formato string y dict para prevenir regresiones.

2. **Corregir el fallback del orquestador:**
   - Implementar la lógica propuesta en el informe: cuando `mapped_action` sea `"n/a"`, llamar a `handle_document_query` y solo devolver la disculpa si no hay resultados útiles.
   - Registrar métricas diferenciadas (`fallback_rag_hit`, `fallback_rag_miss`) para evaluar el impacto.

3. **Regularizar el registro de intenciones:**
   - Añadir `ask_document` a `intents_registry.json` y actualizar sus alias (`documento`, `tramite`, etc.).
   - Ajustar cualquier validación que dependa de la lista canónica (por ejemplo, el auditor de intenciones) para reconocer la nueva entrada.

4. **Ajustes de clasificación y umbrales (seguimiento):**
   - Revisar `UMBRAL_SCORE` y la normalización de etiquetas en `intent_classifier.py` para tolerar tildes o mayúsculas, tal como sugería el informe. 【F:docs/100.500-REINGENERÍA RAG.md†L122-L210】
   - Generar casos de prueba de regresión (`tests/data/intent_regresion.json`) para consultas de trámites y asegurar que clasifican en `ask_document`.

## 5. Próximos pasos sugeridos

- **Validación end-to-end:** Una vez aplicados los fixes, repetir la consulta “Necesito información sobre el permiso de aterrizaje” contra `/orchestrate` y verificar que se devuelve un extracto de `RAG-doc_tramites` o, en su defecto, “No se encontró una respuesta”.
- **Monitoreo:** Incorporar paneles en Grafana que midan el ratio de `intent_registry_miss` y de fallbacks que terminan en respuesta útil.
- **Gestión de datos:** Evaluar si el dataset de trámites necesita alias adicionales para “permiso de aterrizaje” (por ejemplo, “permiso de aterrizaje municipal”) para mejorar el puntaje del IntentEngine.
