# Usamos la imagen base de Node.js LTS (versión 16 en Alpine para mantener la imagen ligera)
FROM node:16-alpine

# Establecemos el directorio de trabajo
WORKDIR /app

# Copiamos el package.json y package-lock.json (si existe)
COPY package*.json ./

# Instalamos las dependencias en modo producción
RUN npm install --production

# Copiamos el resto del código, incluyendo:
# - socketServer.js (servidor de WebSocket)
# - Los directorios static/ y templates/ que contienen los recursos del frontend
COPY . .

# Exponemos el puerto en el que se ejecutará la aplicación (3000, configurado en socketServer.js)
EXPOSE 3000

# Comando para iniciar el servidor
CMD [ "node", "socketServer.js" ]