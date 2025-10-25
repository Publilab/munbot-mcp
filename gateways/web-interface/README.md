# Web-Interface
Este contenedor implementa la interfaz web para interactuar en tiempo real con el chatbot mediante WebSockets.
## Características
* **Interfaz de Chat en Tiempo Real:** Permite la comunicación bidireccional entre el usuario y el backend del chatbot.
* **Recursos Estáticos:** Incluye estilos CSS, scripts JavaScript y archivos de imagen para un frontend moderno y responsivo.
* **Conexión a Microservicios:** Se conecta con el backend del chatbot a través de un servidor WebSocket implementado en Node.js.

## Estructura del Proyecto
```graphql

web-interface/
├── Dockerfile            # Instrucciones para construir la imagen Docker.
├── README.md             # Este archivo.
├── package.json          # Dependencias y scripts de Node.js.
├── socketServer.js       # Servidor WebSocket que envía y recibe mensajes.
├── static/               # Recursos estáticos del frontend.
│   └── css/
│       └── style.css     # Hoja de estilos.
├── script.js             # Lógica del frontend para gestionar la interacción con el WebSocket.
```

## Requisitos
* Docker instalado en el sistema.

## Construcción y Ejecución
### Construir la Imagen
Desde el directorio raíz del microservicio, ejecuta:
```bash
docker build -t web-interface .
```

### Ejecutar el Contenedor
Para correr el contenedor:
```bash
docker run -d --name web-interface -p 3000:3000 web-interface
```

Asegúrate de que el puerto 3000 no esté en uso. El servidor WebSocket está configurado para escuchar en ese puerto.
### Acceder a la Interfaz
Abre tu navegador y accede a [http://localhost:3000](http://localhost:3000/) para interactuar con el chat.
# Configuración Adicional
* **Variables de Entorno:** Si es necesario ajustar parámetros (por ejemplo, la URL del servidor del chatbot), se pueden definir variables de entorno al iniciar el contenedor.
* **Modificaciones de Frontend:** Para actualizar la interfaz, edita los archivos en los directorios static/ y script.js. Posteriormente, reconstruye la imagen para aplicar los cambios.

⠀Solución de Problemas
* **Errores en la Conexión WebSocket:** Revisa la salida del contenedor usando docker logs web-interface.
* **Dependencias:** Verifica que el archivo package.json incluya todas las dependencias necesarias (por ejemplo, express, socket.io, axios) y que se hayan instalado correctamente durante la construcción.
