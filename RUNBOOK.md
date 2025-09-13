# RUNBOOK

## Desactivar agentes

1. Establecer `AGENT_MODE=0`, `AGENT_CANARY_RATIO=0`, `RAG_CATEGORY_AWARE=0`.
2. Reconstruir/recargar servicios.
3. Ejecutar subset del test set para confirmar que las salidas coinciden con `baseline_results.jsonl`.
4. Verificar ausencia de eventos `agent.*` en logs y métricas Prometheus.

