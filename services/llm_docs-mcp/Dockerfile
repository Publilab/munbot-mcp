FROM python:3.11-slim

ENV TZ=America/Santiago \
    TOKENIZERS_PARALLELISM=false \
    OMP_NUM_THREADS=4 \
    SENTENCE_TRANSFORMERS_HOME=/app/models \
    TRANSFORMERS_CACHE=/app/models

RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        build-essential git libopenblas-base netcat-traditional && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install -U pip --no-cache-dir
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir -r requirements.txt

# Descarga del modelo
RUN mkdir -p /app/models && \
    python - <<'PY'
from sentence_transformers import SentenceTransformer
SentenceTransformer("sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2", device="cpu")
PY

# Copia código y scripts (una sola instrucción)
COPY . .

# Asegurar permisos
RUN chmod +x /app/entrypoint.sh

EXPOSE 8000
ENTRYPOINT ["/app/entrypoint.sh"]
