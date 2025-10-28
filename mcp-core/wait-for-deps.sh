#!/usr/bin/env bash
set -e

# Parámetros por defecto (pueden sobreescribirse vía env vars)
REDIS_HOST=${REDIS_HOST:-redis}
REDIS_PORT=${REDIS_PORT:-6379}
POSTGRES_HOST=${POSTGRES_HOST:-postgres}
POSTGRES_PORT=${POSTGRES_PORT:-5432}

echo "→ Esperando a Redis en $REDIS_HOST:$REDIS_PORT..."
until nc -z "$REDIS_HOST" "$REDIS_PORT"; do sleep 2; done

echo "→ Esperando a Postgres en $POSTGRES_HOST:$POSTGRES_PORT..."
until nc -z "$POSTGRES_HOST" "$POSTGRES_PORT"; do sleep 2; done

# AI/Qdrant components removed from deployment; skipping waits for them

echo "→ Todas las dependencias listas. Iniciando mcp‑core..."
exec "$@"
