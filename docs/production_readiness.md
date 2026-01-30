# Producción y Escalamiento — checklist mínimo

## Rollback
- Procedimiento probado (≤ 15 min)
- Scripts/flag listos para revertir

## Escalado
- Escalado horizontal de `mcp-core`
- Escalado de servicios críticos (scheduler, complaints)

## Gates de expansión (nuevo departamento)
- SLO cumplido 2 semanas
- KB cobertura ≥ 70%
- Suite regresión ≥ 95% pass en staging

## Canary
- Activación gradual por feature flag
- Monitoreo p95 + fallback rate
