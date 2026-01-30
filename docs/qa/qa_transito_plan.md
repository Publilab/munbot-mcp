# QA Tránsito v1 — Plan mínimo

## Objetivo
Asegurar que FAQ e interpretativas respondan correctamente antes de producción.

## Alcance
- FAQ + Interpretativas
- Agenda y Reclamos (solo smoke tests)

## Suite de regresión
Archivo: `tests/qa_transito_regression.jsonl`
- Cubre ≥200 preguntas reales.
- Cada caso declara tipo esperado y etiqueta.

## Tipos de pruebas
1) **Regresión**
   - Asegurar que preguntas históricas siguen respondiendo.
2) **Desambiguación**
   - Casos ambiguos deben preguntar.
3) **Fallback**
   - Si no hay fuente, debe derivar sin inventar.
4) **Degradación**
   - Si falla semántica/LLM, debe caer a determinista.
5) **Agenda/Reclamos**
   - Validación de flujo y campos.

## Criterios de aprobación
- ≥95% pass en regresión
- 0 alucinaciones
- Agenda/Reclamos completan sin bloqueo

## Evidencia
Guardar reporte de resultados por fecha.
