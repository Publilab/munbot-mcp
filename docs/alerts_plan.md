# Alertas mínimas (Prometheus)

## Reglas sugeridas
- **Fallback rate > 10%** (5–15 min)
- **Error rate > 2%** (5–15 min)
- **Latencia p95**: web > 2.5s / whatsapp > 5s
- **Agenda éxito < 70%**

Archivo aplicado: `prometheus/alerts.yml`

> Nota: requiere series de éxito/total para agenda/reclamos.
