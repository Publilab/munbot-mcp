#!/usr/bin/env bash
set -e

# Permite configurar host/puerto vía variables de entorno
QDRANT_HOST="${QDRANT_HOST:-qdrant}"
QDRANT_PORT="${QDRANT_PORT:-6333}"

until nc -z "$QDRANT_HOST" "$QDRANT_PORT"; do
  echo "⌛ Esperando a Qdrant en ${QDRANT_HOST}:${QDRANT_PORT}…"
  sleep 1
done
echo "✅ Qdrant listo."

# 1. Crea la colección si no existe
python /app/scripts/init_qdrant.py || true

# 2. Indexa los documentos (este script es idempotente, re-insertar no causa problemas)
python /app/scripts/index_documents.py --docs-dir "${DOCS_DIR:-/app/documents}" --collection "${QDRANT_COLLECTION:-munbot_docs}"

# 3. Lanza la aplicación
exec uvicorn gateway:app --host 0.0.0.0 --port 8000
