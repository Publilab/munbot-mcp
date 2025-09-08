# llm_docs-mcp

Servicio de búsqueda y generación de respuestas basado en documentos usando Qdrant y un modelo LLM local.

## Variables de entorno

- `QDRANT_SIMILARITY_THRESHOLD` (por defecto `0.5`): umbral de similitud para aceptar el mejor resultado de Qdrant. Ajusta este valor entre `0.45` y `0.6` según necesites mayor recall o mayor precisión.
- `LLM_MAX_NEW_TOKENS` (por defecto `150`): cantidad máxima de tokens que generará el modelo al responder.
- `RAG_COLLECTION_FAQ`, `RAG_COLLECTION_TRAMITES`, `RAG_COLLECTION_NORMATIVA`: nombres de las colecciones en Qdrant que se usarán para responder preguntas de tipo FAQ, trámites y normativa respectivamente.
- `RAG_CATEGORY_AWARE` (`0` o `1`): si se activa (`1`), el servicio seleccionará automáticamente la colección y el prompt según la categoría indicada en la consulta (`categoria`).

Configura estas variables en tu entorno o en `compose/.env` según tus necesidades.

## Parámetros de entrada

Las herramientas que procesan texto esperan el parámetro `texto` como entrada
estándar. Para mantener compatibilidad con integraciones anteriores también se
acepta el alias `consulta`.

