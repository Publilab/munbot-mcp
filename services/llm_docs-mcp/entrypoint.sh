#!/usr/bin/env bash
set -e

QDRANT_HOST="qdrant"
QDRANT_PORT="6333"

until nc -z "$QDRANT_HOST" "$QDRANT_PORT"; do
  echo "⌛ Esperando a Qdrant en ${QDRANT_HOST}:${QDRANT_PORT}…"
  sleep 1
done
echo "✅ Qdrant listo."

python /app/scripts/init_qdrant.py

exec uvicorn gateway:app --host 0.0.0.0 --port 8000
