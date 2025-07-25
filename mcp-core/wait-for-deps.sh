#!/usr/bin/env bash
set -e

# Parámetros por defecto (pueden sobreescribirse vía env vars)
REDIS_HOST=${REDIS_HOST:-redis}
REDIS_PORT=${REDIS_PORT:-6379}
POSTGRES_HOST=${POSTGRES_HOST:-postgres}
POSTGRES_PORT=${POSTGRES_PORT:-5432}
QDRANT_HOST=${QDRANT_HOST:-qdrant}
QDRANT_PORT=${QDRANT_PORT:-6333}
LLM_DOCS_HOST=${LLM_DOCS_MCP_HOST:-llm_docs-mcp}
LLM_DOCS_PORT=${LLM_DOCS_MCP_PORT:-8000}
LLM_DOCS_HEALTH_URL=${LLM_DOCS_MCP_HEALTH_URL:-http://$LLM_DOCS_HOST:$LLM_DOCS_PORT/health}

echo "→ Esperando a Redis en $REDIS_HOST:$REDIS_PORT..."
until nc -z "$REDIS_HOST" "$REDIS_PORT"; do sleep 2; done

echo "→ Esperando a Postgres en $POSTGRES_HOST:$POSTGRES_PORT..."
until nc -z "$POSTGRES_HOST" "$POSTGRES_PORT"; do sleep 2; done

echo "→ Esperando a Qdrant en $QDRANT_HOST:$QDRANT_PORT..."
until nc -z "$QDRANT_HOST" "$QDRANT_PORT"; do sleep 2; done

echo "→ Esperando a llm_docs-mcp (puerto TCP) en $LLM_DOCS_HOST:$LLM_DOCS_PORT..."
until nc -z "$LLM_DOCS_HOST" "$LLM_DOCS_PORT"; do sleep 2; done

echo "→ Esperando al endpoint de salud de llm_docs-mcp en $LLM_DOCS_HEALTH_URL..."
until curl -sf "$LLM_DOCS_HEALTH_URL" >/dev/null 2>&1; do sleep 2; done

echo "→ Todas las dependencias listas. Iniciando mcp‑core..."
exec uvicorn orchestrator:app --host 0.0.0.0 --port 5000
