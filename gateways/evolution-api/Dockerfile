FROM node:18.20.8-alpine

WORKDIR /app

# Instalar dependencias de compilación y curl
RUN apk add --no-cache python3 g++ make curl

# Copiar e instalar dependencias
COPY package*.json ./
RUN npm install --force --production

# Copiar código fuente
COPY . .

# Puerto expuesto
EXPOSE 8080

# Comando de inicio
CMD ["node", "src/index.js"]