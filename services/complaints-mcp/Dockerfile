# Usar una imagen base ligera de Python
FROM python:3.9-slim

# Configurar zona horaria para evitar errores
ENV TZ=America/Chile/Santiago
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# Crear usuario no root
RUN useradd -m -d /home/appuser -s /bin/bash appuser

# Instalar dependencias del sistema necesarias para compilar paquetes Python
RUN apt-get update && apt-get install -y gcc libssl-dev build-essential libffi-dev curl && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY api.py ./
COPY .env ./
COPY repository.py ./
COPY models.py ./
COPY requirements.txt ./
COPY utils ./utils
COPY ./databases/init-postgres.sql ./databases/init-postgres.sql
RUN pip install --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt

# Copiar código y configuraciones
COPY . .

# Cambiar permisos de trabajo
RUN chown -R appuser:appuser /app

# Exponer puerto
EXPOSE 7000

# Cambiar a usuario no root
USER appuser

# Ejecutar la aplicación con Gunicorn
CMD ["gunicorn", "-w", "4", "-b", "0.0.0.0:7000", "api:app"]