# Revisión de Pendientes Interpretativas

**Fecha**: 2026-01-26

## Estado actual
- No existen registros en `databases/interpretativas/pendientes_transito.jsonl`.
- No existe caché semántico curado en `databases/interpretativas/cache_transito.jsonl`.

## Qué se revisa aquí (cuando haya tráfico real)
1. **Pendientes** (`pendientes_<depto>.jsonl`): preguntas nuevas sin respuesta directa o con baja confianza.
2. **Auto‑cache** (`cache_<depto>.jsonl`): respuestas generadas automáticamente para revisión y aprobación.

## Criterio de revisión (resumen)
- Verificar que la respuesta esté respaldada por documento oficial.
- Si la respuesta es correcta, mover/duplicar a `cache_<depto>.jsonl` con `"status": "approved"`.
- Si falta información, redactar respuesta oficial y agregarla al JSON interpretativo correspondiente.

## Próximos pasos
- Activar tráfico real y dejar que el pipeline genere los archivos de pendientes.
- Revisar semanalmente y aprobar respuestas útiles.
