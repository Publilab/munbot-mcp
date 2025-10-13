# MunBoT Metrics

MunBoT exposes Prometheus metrics on `/metrics` from each microservice.

| Metric | Description |
| ------ | ----------- |
| `munbot_requests_total` | Contador de peticiones procesadas etiquetado por intent. |
| `munbot_fallbacks_total` | Número total de fallbacks activados. |
| `munbot_human_escalations_total` | Total de veces que la conversación se escaló a un humano. |
| `munbot_errors_total` | Errores ocurridos en microservicios, etiquetados por intent. |
| `mcp_microservice_errors_total` | Errores de orquestador al llamar microservicios. |
| `rag_latency_seconds` | Histograma de latencia para las consultas RAG. |
| `munbot_fallback_rag_hit_total` | Intent `n/a` resuelto con RAG (éxito). |
| `munbot_fallback_rag_miss_total` | Intent `n/a` sin resultados útiles en RAG. |

El ratio de fallback puede calcularse como `munbot_fallbacks_total/munbot_requests_total`.
