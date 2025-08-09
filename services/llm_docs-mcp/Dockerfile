FROM python:3.11-slim

ENV TZ=America/Santiago \
    TOKENIZERS_PARALLELISM=false \
    OMP_NUM_THREADS=4 \
    EMBEDDING_MODEL_NAME="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2" \
    SENTENCE_TRANSFORMERS_HOME=/app/models \
    HF_HOME=/app/models

RUN ln -snf /usr/share/zoneinfo/${TZ} /etc/localtime \
 && echo ${TZ} > /etc/timezone

# build-essential es necesario para compilar algunas dependencias como llama-cpp-python
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        build-essential libopenblas0 netcat-traditional && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN mkdir -p /app/logs

COPY services/llm_docs-mcp/requirements.txt .
RUN pip install -U pip --no-cache-dir
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu
# Instala dependencias, incluyendo llama-cpp-python para GGUF
RUN pip install --no-cache-dir -r requirements.txt

# Pre-descarga el modelo de embeddings para acelerar el arranque del contenedor
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('${EMBEDDING_MODEL_NAME}', device='cpu')"

# Copia código y scripts (una sola instrucción)
COPY services/llm_docs-mcp/ ./

# asumiendo que en tu repo tienes ../mcp-core/prompts y ../mcp-core/tool_schemas
COPY mcp-core/prompts/ ./prompts
COPY services/llm_docs-mcp/models/ ./models
COPY mcp-core/tool_schemas/ /app/tool_schemas

ENV PROMPTS_PATH=/app/prompts
ENV TOOLS_PATH=/app/tool_schemas

# El modelo GGUF se monta como un volumen, no se define aquí.
# N_THREADS se puede configurar en docker-compose.yml

# Asegurar permisos
RUN chmod +x /app/entrypoint.sh

EXPOSE 8000
ENTRYPOINT ["/app/entrypoint.sh"]
