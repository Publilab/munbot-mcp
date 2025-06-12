FROM python:3.11-slim

# Configurar zona horaria
ENV TZ=America/Santiago
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# 1. Instala dependencias del sistema (incluye git)
RUN apt-get update && apt-get install -y \
    build-essential cmake libopenblas-dev git && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copia todo el código y el modelo
COPY . .

# 2. Instala las dependencias de Python
RUN pip install --upgrade pip setuptools wheel

# 3. Instala llama-cpp-python usando OpenBLAS
ENV CMAKE_ARGS="-DLLAMA_BLAS=ON -DLLAMA_BLAS_VENDOR=OpenBLAS"
RUN pip install llama-cpp-python

# 4. Instala el resto de dependencias de tu proyecto
RUN pip install -r requirements.txt

RUN python -m nltk.downloader punkt || true

COPY documents/ ./documents/
COPY gateway.py .
COPY .env ./
COPY models/ ./models

EXPOSE 8000

CMD ["uvicorn", "gateway:app", "--host", "0.0.0.0", "--port", "8000"]