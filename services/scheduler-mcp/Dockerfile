# Base image
FROM python:3.10-slim

# Configurar zona horaria de Santiago
ENV TZ=America/Santiago
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# Configuración del directorio de trabajo
WORKDIR /app

# Copiar archivos del proyecto (sin generate_appointments.py)
COPY requirements.txt ./
COPY app.py ./
COPY tasks.py ./
COPY notifications.py ./

# Instalar dependencias del sistema necesarias para compilar paquetes Python
RUN apt-get update && apt-get install -y gcc libssl-dev build-essential libffi-dev curl && rm -rf /var/lib/apt/lists/*

# Instalar dependencias
RUN pip install --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt && pip install gunicorn

# Crear directorio para Prometheus multiproceso
RUN mkdir -p /tmp/prometheus_multiproc_dir
ENV PROMETHEUS_MULTIPROC_DIR=/tmp/prometheus_multiproc_dir

# Configurar punto de entrada
EXPOSE 6001

CMD ["gunicorn", "-k", "uvicorn.workers.UvicornWorker", "-w", "2", "-b", "0.0.0.0:6001", "app:app"]