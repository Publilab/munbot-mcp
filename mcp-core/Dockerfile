FROM python:3.10-slim

# Zona horaria
ENV TZ=America/Santiago
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# 1) Sistema
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      build-essential cmake libopenblas-dev git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /mcp-core

# 2) Python – copiar e instalar deps
COPY requirements.txt ./
RUN pip install --upgrade pip setuptools wheel

# Instalar PyTorch CPU
RUN pip install torch --index-url https://download.pytorch.org/whl/cpu

# Instalación explícita para garantizar que huggingface-hub existe
RUN pip install llama-cpp-python huggingface-hub sentencepiece
# Resto de dependencias, incluidas fastapi, redis, etc.
RUN pip install --no-cache-dir -r requirements.txt

# 3) Código de la aplicación
COPY .env orchestrator.py context_manager.py ./
COPY tool_schemas/ ./tool_schemas/
COPY prompts/ ./prompts/

ENV NO_PROXY=localhost,127.0.0.1
ENV no_proxy=localhost,127.0.0.1

EXPOSE 5000
CMD ["uvicorn", "orchestrator:app", "--host", "0.0.0.0", "--port", "5000"]