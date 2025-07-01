# Usa la variante Debian slim de Node para evitar issues de APK
FROM node:18.20.8-slim

WORKDIR /app

# 1) Instala Python3, curl y herramientas de compilación via apt
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      python3 \
      curl \
      build-essential \
    && rm -rf /var/lib/apt/lists/*

# 2) Copia e instala dependencias de Node
COPY package*.json ./
COPY .env ./
RUN npm ci --only=production

# 3) Copia el resto del código fuente
COPY . .

# 4) Exponer el puerto de tu app
EXPOSE 8080

# 5) Comando de inicio
CMD ["node", "src/index.js"]
