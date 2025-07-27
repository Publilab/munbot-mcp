FROM python:3.11-slim

ENV TZ=America/Santiago \
    TOKENIZERS_PARALLELISM=false \
    OMP_NUM_THREADS=4 \
    SENTENCE_TRANSFORMERS_HOME=/app/models \
    HF_HOME=/app/models

RUN ln -snf /usr/share/zoneinfo/${TZ} /etc/localtime \
 && echo ${TZ} > /etc/timezone

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        build-essential git git-lfs libopenblas0 netcat-traditional && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install -U pip --no-cache-dir
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu
# Instala dependencias de requirements.txt (que ya no incluye auto-gptq)
RUN pip install --no-cache-dir -r requirements.txt

# Instala auto-gptq desde el índice recomendado para compatibilidad con CPU, asegurando que se instale correctamente.
# Se añade BUILD_CUDA_EXT=0 para forzar la compilación para CPU y evitar que busque CUDA_HOME,
# especialmente en arquitecturas (como arm64) que no tienen wheels pre-compilados.
RUN BUILD_CUDA_EXT=0 pip install --no-cache-dir auto-gptq --extra-index-url https://huggingface.github.io/autogptq-index/whl/cpu/

# Clona el modelo 4-bit
RUN git lfs install && \
    git clone --depth 1 \
    https://huggingface.co/TheBloke/Llama-2-7B-GPTQ \
    /models/Llama-2-7B-GPTQ

# Descarga del modelo
RUN python - <<'PY'
from sentence_transformers import SentenceTransformer
SentenceTransformer("sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2", device="cpu")
PY

# Copia código y scripts (una sola instrucción)
COPY . .

# asumiendo que en tu repo tienes ../mcp-core/prompts y ../mcp-core/tool_schemas
COPY prompts/ ./prompts
COPY tool_schemas/ /app/tool_schemas

ENV PROMPTS_PATH=/app/prompts
ENV TOOLS_PATH=/app/tool_schemas

# Variables de entorno para carga de modelo
ENV MODEL_PATH=/models/Llama-2-7B-GPTQ
ENV LLAMA_N_THREADS=4

# Asegurar permisos
RUN chmod +x /app/entrypoint.sh

EXPOSE 8000
ENTRYPOINT ["/app/entrypoint.sh"]
