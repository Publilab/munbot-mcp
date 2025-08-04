# llm_docs-mcp

Servicio de búsqueda y generación de respuestas basado en documentos usando Qdrant y un modelo LLM local.

## Variables de entorno

- `QDRANT_SIMILARITY_THRESHOLD` (por defecto `0.5`): umbral de similitud para aceptar el mejor resultado de Qdrant. Ajusta este valor entre `0.45` y `0.6` según necesites mayor recall o mayor precisión.
- `LLM_MAX_NEW_TOKENS` (por defecto `150`): cantidad máxima de tokens que generará el modelo al responder.

Configura estas variables en tu entorno o en `compose/.env` según tus necesidades.

