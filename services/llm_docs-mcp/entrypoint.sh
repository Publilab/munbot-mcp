#!/usr/bin/env bash
set -e

# Esperar Qdrant
until curl -fsS "http://${QDRANT_HOST:-qdrant}:${QDRANT_PORT:-6333}/collections" >/dev/null; do
  echo "⌛ Waiting for Qdrant..."
  sleep 1
done
echo "✅ Qdrant is ready."

if [ "${AUTO_INDEX:-0}" = "1" ]; then
  echo "🚀 Starting auto-indexing..."
  python /app/services/llm_docs-mcp/scripts/init_qdrant.py
  python /app/services/llm_docs-mcp/scripts/index_documents.py \
    --collection "${COLLECTION:-munbot_docs}" \
    --docs-dir "${DOCS_DIR:-/app/services/llm_docs-mcp/documents}"
  echo "✅ Indexing complete."
fi

# Start the application
exec uvicorn gateway:app --host 0.0.0.0 --port 8000
