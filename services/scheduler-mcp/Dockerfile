# Usa una imagen base liviana y moderna
FROM python:3.10-slim

# Configura la zona horaria (opcional)
ENV TZ=America/Santiago
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# Define el directorio de trabajo dentro del contenedor
WORKDIR /app

# Copia los archivos y carpetas del servicio al contenedor
COPY requirements.txt ./
COPY .env ./
COPY app.py ./
COPY db.py ./
COPY gen_schema.py ./
COPY tasks.py ./
COPY notifications.py ./
COPY service.py ./
COPY repository.py ./
COPY wait-for-it.sh ./
COPY utils/ ./utils/
COPY ./databases/init-appointments.sql ./databases/init-appointments.sql

# Si tienes mcp-utils, añádelo aquí (opcional, si usas tracing distribuido)
# COPY ../mcp-utils ./mcp-utils

# Instala dependencias del sistema necesarias para compilar paquetes Python
RUN apt-get update && apt-get install -y gcc libssl-dev build-essential libffi-dev curl netcat-openbsd && rm -rf /var/lib/apt/lists/*

# Instala dependencias de Python
RUN pip install --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt && pip install gunicorn

# (Opcional) Añade mcp-utils al PYTHONPATH si usas el módulo de auditoría compartido
# ENV PYTHONPATH="/app:/app/mcp-utils"

# (Opcional) Habilita el tracing distribuido solo si lo necesitas
# ENV AUDIT=1

# Crea directorio para Prometheus multiproceso si lo usas
RUN mkdir -p /tmp/prometheus_multiproc_dir
ENV PROMETHEUS_MULTIPROC_DIR=/tmp/prometheus_multiproc_dir

# Expón el puerto de la app
EXPOSE 6001

# Da permisos de ejecución al script de espera
RUN chmod +x ./wait-for-it.sh

# Comando de arranque: espera a postgres y luego lanza Gunicorn + Uvicorn Worker + FastAPI
CMD ["./wait-for-it.sh", "postgres:5432", "--", "gunicorn", "-k", "uvicorn.workers.UvicornWorker", "-w", "2", "-b", "0.0.0.0:6001", "app:app"]