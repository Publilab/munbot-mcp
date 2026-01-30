# Observabilidad Tránsito v1 (mínimo viable)

## Métricas (Prometheus)
- `munbot_requests_total` (por intent/app)
- `munbot_fallback_total`
- `munbot_errors_total`
- `munbot_latency_ms` (p95)
- `munbot_agenda_success_total`
- `munbot_reclamo_success_total`
- `scheduler_attempt_total`
- `complaints_attempt_total`
- `munbot_disambiguation_total`
 - `munbot_success_by_category_total`

## Alertas mínimas
- Fallback rate > 10% (5–15 min)
- Error rate > 2% (5–15 min)
- Latencia p95 > 2.5s web / 5s whatsapp
- Agenda éxito < 70%

## Dashboard
Base: `grafana/dashboards/munbot-health.json` (paneles mínimos añadidos: fallback rate, error rate, p95).

## Evidencia
Reporte semanal con top gaps y fricción.
