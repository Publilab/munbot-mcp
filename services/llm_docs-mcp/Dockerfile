# ──────────────────────────────────────────────────────────────
# Imagen base mínima con Python 3.11 (CPU) + OpenBLAS
# ──────────────────────────────────────────────────────────────
FROM python:3.11-slim

# Zona horaria ─ evita warn de datetime en logs
ENV TZ=America/Santiago
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# ──────────────────────────────────────────────────────────────
# Paquetes de sistema esenciales
# ──────────────────────────────────────────────────────────────
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        build-essential  \
        git \
        libopenblas-base \
    && rm -rf /var/lib/apt/lists/*

# ──────────────────────────────────────────────────────────────
# Copiamos primero sólo requirements para aprovechar capa-cache
# ──────────────────────────────────────────────────────────────
WORKDIR /app

COPY requirements.txt .

# 1) actualizamos pip
RUN pip install --no-cache-dir -U pip

# 2) instalamos PyTorch CPU build *antes* para evitar wheels CUDA
RUN pip install --no-cache-dir \
    torch --index-url https://download.pytorch.org/whl/cpu

# 3) instalamos resto de dependencias del proyecto
RUN pip install --no-cache-dir -r requirements.txt

# ──────────────────────────────────────────────────────────────
# Descarga del modelo MiniLM (opcional pero acelera primer arranque)
# ──────────────────────────────────────────────────────────────
RUN python - <<'PY'
from sentence_transformers import SentenceTransformer
SentenceTransformer("sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
                    device='cpu')
PY

# ──────────────────────────────────────────────────────────────
# Copiamos el resto del código y recursos
# ──────────────────────────────────────────────────────────────
COPY .env ./
COPY . .
# (si `documents/` se genera en runtime, basta el mkdir; si contiene seed files déjalo)
RUN mkdir -p documents prompts tools models

# ──────────────────────────────────────────────────────────────
# Variable para desactivar paralelismo excesivo de tokenizers
# ──────────────────────────────────────────────────────────────
ENV TOKENIZERS_PARALLELISM=false \
    OMP_NUM_THREADS=4         # ajusta según CPU del host

EXPOSE 8000

CMD ["uvicorn", "gateway:app", "--host", "0.0.0.0", "--port", "8000"]
