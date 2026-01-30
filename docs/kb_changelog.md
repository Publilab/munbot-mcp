# KB Changelog

Formato mínimo por entrada:
- Fecha (YYYY-MM-DD)
- Archivo / ID
- Cambio
- Responsable
- Estado (draft/revisión/aprobado/publicado)

---
2026-01-29 | apps/faq/kb/transito/*.json + apps/interpretativas/kb/transito/*.json | Se agregan campos de gobernanza (status/updated_at/approved_by) | system | draft
2026-01-29 | apps/faq/kb/transito/*.json + apps/interpretativas/kb/transito/*.json | Se agregan campos de metadata por entrada (entry_*) | system | draft
2026-01-29 | scripts/validate_kb_governance.py | Se agrega validador de gobernanza KB | system | draft
2026-01-29 | docs/inventario_fuentes_transito.md | Se crea inventario inicial de fuentes oficiales | system | draft
2026-01-29 | docs/kb_workflow.md | Se define workflow mínimo de gobernanza KB | system | draft
2026-01-29 | apps/faq/kb/transito/*.json + apps/interpretativas/kb/transito/*.json | Se asigna owner municipal y vigencia por regla (1 año desde publicación) | system | draft
2026-01-29 | apps/faq/kb/transito/*.json + apps/interpretativas/kb/transito/*.json | Se asignan fuentes base por archivo y se marca “observacion” donde falta fuente local | system | draft
2026-01-29 | docs/observability_plan.md | Se define plan mínimo de observabilidad y alertas | system | draft
2026-01-29 | docs/slo_sla.md | Se define SLO/SLA mínimos Tránsito v1 | system | draft
2026-01-29 | docs/production_readiness.md | Checklist de producción y escalamiento | system | draft
2026-01-29 | docs/feature_flags_runtime.md | Se documentan feature flags mínimos de degradación | system | draft
2026-01-29 | mcp-core/settings.py + mcp-core/interpretativas_engine.py + mcp-core/orchestrator.py | Se agregan flags de degradación (FAQ_ONLY, embeddings, rerank) | system | draft
2026-01-29 | .env + .env.example + .env.sample | Se agregan flags de degradación en variables de entorno | system | draft
2026-01-29 | mcp-core/orchestrator.py | Se agregan métricas de desambiguación y éxito por categoría | system | draft
2026-01-29 | mcp-core/orchestrator.py | Se contabilizan errores de microservicios | system | draft
2026-01-29 | docs/alerts_plan.md | Se define plan de alertas mínimo | system | draft
2026-01-29 | grafana/dashboards/munbot-health.json | Se agregan paneles de fallback/error/p95 | system | draft
2026-01-29 | prometheus/alerts.yml + prometheus/prometheus.yml | Se activan reglas de alertas | system | draft
2026-01-29 | docs/reporting_plan.md | Se define plan de reportes mensuales | system | draft
2026-01-29 | docs/dataset_pipeline.md + scripts/export_interpretativas_pending.py | Se define pipeline mínimo de dataset y exportador | system | draft
2026-01-29 | docs/report_template.md | Se agrega plantilla de reportes mensuales | system | draft
2026-01-29 | docs/top_50_template.md | Se agrega plantilla de top 50 demanda | system | draft
2026-01-29 | docs/taller_levantamiento.md | Se agrega guía de taller de levantamiento | system | draft
2026-01-29 | docs/tono_institucional.md | Se agrega guía mínima de tono institucional | system | draft
2026-01-29 | docs/catalogo_tramites_template.md | Se agrega plantilla de catálogo canónico | system | draft
2026-01-29 | docs/faq_db_template.md | Se agrega plantilla FAQ DB | system | draft
2026-01-29 | docs/matriz_cobertura_template.md | Se agrega plantilla matriz de cobertura | system | draft
2026-01-29 | docs/aliases_template.md | Se agrega plantilla diccionario de aliases | system | draft
2026-01-29 | scripts/validate_faq_db.py | Se agrega validador FAQ DB | system | draft
2026-01-29 | scripts/validate_matriz_cobertura.py | Se agrega validador matriz de cobertura | system | draft
2026-01-29 | mcp-core/orchestrator.py | Se agregan métricas de intentos agenda/reclamos | system | draft
2026-01-29 | docs/faq_db.json | Se crea archivo base de FAQ DB | system | draft
2026-01-29 | docs/matriz_cobertura.json | Se crea archivo base de matriz de cobertura | system | draft
2026-01-29 | docs/faq_db.json | Se pobla FAQ DB con 50 entradas desde dataset | system | draft
2026-01-29 | docs/matriz_cobertura.json | Se pobla matriz de cobertura con 50 filas | system | draft
