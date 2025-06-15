FROM python:3.11-slim

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

# Instalar frameworks de ML
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir tensorflow
RUN pip install --no-cache-dir flax

# Instalación explícita para garantizar que transformers y huggingface-hub existen
RUN pip install --no-cache-dir transformers huggingface-hub
# Instalar sentencepiece para LlamaTokenizer
RUN pip install --no-cache-dir sentencepiece
RUN pip install --no-cache-dir -r requirements.txt

# Copiar código fuente
COPY .env ./
COPY . .
COPY documents/ ./documents/
COPY models/Llama-3.2-3B-Instruct-Q6_K.gguf ./models/Llama-3.2-3B-Instruct-Q6_K.gguf

# Crear directorios necesarios
RUN mkdir -p documents
RUN mkdir -p prompts
RUN mkdir -p tools
RUN mkdir -p models

# Usar un tokenizador público que no requiere autenticación
RUN python -c "from transformers import AutoTokenizer; AutoTokenizer.from_pretrained('hf-internal-testing/llama-tokenizer')"

# Exponer puerto
EXPOSE 8000

# Comando para iniciar la aplicación
CMD ["uvicorn", "gateway:app", "--host", "0.0.0.0", "--port", "8000"]