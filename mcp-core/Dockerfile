# mcp-core/Dockerfile
FROM python:3.10-slim

# ───────────────────────────────
# 1) Zona horaria y sistema base
# ───────────────────────────────
ENV TZ=America/Santiago
RUN ln -snf /usr/share/zoneinfo/${TZ} /etc/localtime \
 && echo ${TZ} > /etc/timezone \
 && apt-get update \
 && apt-get install -y --no-install-recommends \
        git build-essential cmake gcc g++ \
        libblas-dev liblapack-dev \
 && rm -rf /var/lib/apt/lists/*

# ───────────────────────────────
# 2) Dependencias Python
# ───────────────────────────────
WORKDIR /app

#   Copiamos sólo requirements para aprovechar la cache
COPY mcp-core/requirements.txt ./requirements.txt
RUN pip install --upgrade pip setuptools wheel \
 && pip install torch --index-url https://download.pytorch.org/whl/cpu \
 && pip install --no-cache-dir -r requirements.txt

# ───────────────────────────────
# 3) Código de la aplicación
# ───────────────────────────────
#   Copiamos de una sola vez los directorios que necesitamos
COPY mcp-core/ ./
COPY mcp-core/services/ ./services/
COPY mcp-core/databases/ ./databases/
COPY mcp-core/utils/ ./utils/
COPY mcp-core/tests/ ./tests/

# ───────────────────────────────
# 4) Variables de entorno y puertos
# ───────────────────────────────
ENV NO_PROXY=localhost,127.0.0.1
ENV no_proxy=localhost,127.0.0.1

EXPOSE 5000

# ───────────────────────────────
# 5) Comando de arranque
# ───────────────────────────────
CMD ["uvicorn", "orchestrator:app", "--host", "0.0.0.0", "--port", "5000"]
