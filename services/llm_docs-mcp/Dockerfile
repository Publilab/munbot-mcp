FROM python:3.10-slim

# Zona horaria
ENV TZ=America/Santiago
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# Instalar dependencias del sistema
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    build-essential \
    cmake \
    git \
    libopenblas-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copiar e instalar dependencias
COPY requirements.txt .
RUN pip install --no-cache-dir -U pip
# Instalación explícita para garantizar que transformers y huggingface-hub existen
RUN pip install --no-cache-dir transformers huggingface-hub
RUN pip install --no-cache-dir -r requirements.txt

# Copiar código fuente
COPY . .

# Crear directorios necesarios
RUN mkdir -p documents
RUN mkdir -p prompts
RUN mkdir -p tools
RUN mkdir -p models

# Descargar modelo si no existe (esto puede tardar)
RUN python -c "from huggingface_hub import snapshot_download; from os.path import exists; \
    if not exists('models/Llama-3.2-3B-Instruct-Q6_K.gguf'): \
        snapshot_download(repo_id='bartowski/Llama-3.2-3B-Instruct-GGUF', \
        local_dir='models', allow_patterns=['Llama-3.2-3B-Instruct-Q6_K.gguf'])"

# Descargar tokenizer de Hugging Face si no existe
RUN python -c "from transformers import LlamaTokenizer; LlamaTokenizer.from_pretrained('meta-llama/Llama-2-7b-hf')"

# Exponer puerto
EXPOSE 8000

# Comando para iniciar la aplicación
CMD ["uvicorn", "gateway:app", "--host", "0.0.0.0", "--port", "8000"]