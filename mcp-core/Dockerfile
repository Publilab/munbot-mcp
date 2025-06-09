# Usar una imagen base oficial de Python
FROM python:3.10-slim

# Establecer el directorio de trabajo
WORKDIR /mcp-core

# Asegurar que el directorio de trabajo esté en el PYTHONPATH
ENV PYTHONPATH="${PYTHONPATH}:/mcp-core"

# Copiar requirements si existiera (puedes agregarlo luego)
COPY orchestrator.py ./
COPY context_manager.py ./
COPY tool_schemas ./tool_schemas/
COPY prompts ./prompts
COPY requirements.txt ./
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    cmake \
    gcc \
    g++ \
    python3-dev \
    git \
    && rm -rf /var/lib/apt/lists/* \
    && export CC=gcc \
    && export CXX=g++ \
    && pip install --upgrade pip setuptools wheel \
    && pip install --no-cache-dir -r requirements.txt

# Exponer el puerto de la API
EXPOSE 5000

RUN ls -l /mcp-core

# Comando para lanzar el servidor FastAPI
CMD ["uvicorn", "orchestrator:app", "--host", "0.0.0.0", "--port", "5000"] 