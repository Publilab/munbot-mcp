# mcp-core/Dockerfile
FROM python:3.10-slim

# ───────────────────────────────
# 1) Zona horaria y sistema base
# ───────────────────────────────
ENV TZ=America/Santiago
RUN ln -snf /usr/share/zoneinfo/${TZ} /etc/localtime \
 && echo ${TZ} > /etc/timezone
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    git \
    libopenblas0 \
    curl \
    netcat-openbsd \
  && rm -rf /var/lib/apt/lists/*

# ───────────────────────────────
# 2) Dependencias Python
# ───────────────────────────────
ENV PYTHONUNBUFFERED=1 PYTHONPATH=/app
WORKDIR /app

#   Copiamos sólo requirements para aprovechar la cache
COPY mcp-core/requirements.txt /app/requirements.txt
RUN pip install --upgrade pip setuptools wheel \
 && pip install torch --index-url https://download.pytorch.org/whl/cpu \
 && pip install --no-cache-dir -r requirements.txt

# ───────────────────────────────
# 3) Código de la aplicación
# ───────────────────────────────
COPY . /app/

RUN chmod +x /app/mcp-core/wait-for-deps.sh
ENTRYPOINT ["/app/mcp-core/wait-for-deps.sh"]

# ───────────────────────────────
# 4) Variables de entorno y puertos
# ───────────────────────────────
ENV NO_PROXY=localhost,127.0.0.1
ENV no_proxy=localhost,127.0.0.1

EXPOSE 5000

# ───────────────────────────────
# 5) Comando de arranque
# ───────────────────────────────
CMD ["uvicorn", "mcp-core.orchestrator:app", "--host", "0.0.0.0", "--port", "5000"]