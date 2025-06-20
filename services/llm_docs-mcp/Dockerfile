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

# Instalar dependencias
RUN pip install --no-cache-dir -r requirements.txt

# Copiar código fuente
COPY .env ./
COPY . .
COPY documents/ ./documents/

# Crear directorios necesarios
RUN mkdir -p documents
RUN mkdir -p prompts
RUN mkdir -p tools
RUN mkdir -p models


# Exponer puerto
EXPOSE 8000

# Comando para iniciar la aplicación
CMD ["uvicorn", "gateway:app", "--host", "0.0.0.0", "--port", "8000"]