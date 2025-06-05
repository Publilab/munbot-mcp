// web_app/socketServer.js

const express = require('express');
const http = require('http');
const { Server } = require('socket.io');
const axios = require('axios');
const path = require('path');

const app = express();
const server = http.createServer(app);

// Servir archivos estáticos desde /static
app.use('/static', express.static(path.join(__dirname, 'static')));

// Servir index.html desde /templates al acceder a /
app.get('/', (req, res) => {
  res.sendFile(path.join(__dirname, 'templates', 'index.html'));
});

// Configuración de CORS directamente en la instancia de Socket.IO
const io = new Server(server, {
    cors: {
        origin: "*", // Permite todas las orígenes. Para producción, especifica los dominios permitidos.
        methods: ["GET", "POST"]
    }
});

const MCP_URL = process.env.MCP_URL || 'http://mcp-core:5000/orchestrate'; // Nueva URL del MCP

io.on('connection', (socket) => {
    console.log('Un usuario se ha conectado');

    socket.on('message', async (msg) => {
        console.log('Mensaje recibido del cliente:', msg);
        try {
            // Construir el payload para el MCP
            const payload = {
                pregunta: msg,
                context: { sender: socket.id }
            };
            // Enviar el mensaje al MCP
            const response = await axios.post(MCP_URL, payload);
            if (response.data && response.data.respuesta) {
                socket.emit('bot_message', response.data.respuesta);
            } else if (response.data && response.data.message) {
                socket.emit('bot_message', response.data.message);
            } else {
                socket.emit('bot_message', 'No se recibió respuesta válida del MCP.');
            }
        } catch (error) {
            console.error('Error al comunicarse con el MCP:', error);
            socket.emit('bot_message', 'Lo siento, hubo un error procesando tu solicitud.');
        }
    });

    socket.on('disconnect', () => {
        console.log('Un usuario se ha desconectado');
    });
});

const PORT = process.env.PORT || 3000;
server.listen(PORT, () => {
    console.log(`Servidor WebSocket escuchando en el puerto ${PORT}`);
});
