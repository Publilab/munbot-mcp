// web_app/socketServer.js

require('dotenv').config();

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
const MCP_TIMEOUT = parseInt(process.env.MCP_TIMEOUT || '15000', 10);

async function sendToMCP(payload) {
    return axios.post(MCP_URL, payload, { timeout: MCP_TIMEOUT });
}

io.on('connection', (socket) => {
    console.log('Un usuario se ha conectado');
    // Inicializar session_id por socket
    socket.sessionId = null;

    socket.on('message', async (msg) => {
        console.log('Mensaje recibido del cliente:', msg);
        try {
            // Construir el payload para el MCP. La session_id debe enviarse en la
            // raiz del JSON para que el orquestador pueda recuperarla.
            const payload = {
                pregunta: msg,
                context: { sender: socket.id },
                session_id: socket.sessionId, // USAR sessionId del socket
                channel: 'web'
            };
            // Enviar el mensaje al MCP con un único intento
            const response = await sendToMCP(payload);
            // Actualizar el identificador de sesion si es devuelto por el MCP
            if (response.data) {
                socket.sessionId = response.data.session_id || socket.sessionId;
            }
            const data = response.data || {};
            if (Array.isArray(data.respuestas)) {
                // Manejar una lista de respuestas de cualquier longitud
                data.respuestas.forEach((botMsg, index) => {
                    setTimeout(() => {
                        socket.emit('bot_message', botMsg);
                    }, index * 1200); // Pausa de 1.2 segundos entre mensajes
                });
            } 
            else if (data.respuesta) {
                socket.emit('bot_message', data.respuesta);
            }
            else if (data.message) {
                socket.emit('bot_message', data.message);
            }
            else {
                socket.emit('bot_message', 'No se recibió respuesta válida del MCP.');
            }
        } catch (error) {
            console.error('Error al comunicarse con el MCP:', error);
            socket.emit('bot_message', 'Lo siento, hubo un error procesando tu solicitud.');
        }
    });

    socket.on('disconnect', () => {
        console.log('Un usuario se ha desconectado');
        socket.sessionId = null; // Limpiar solo la sesión de este socket
    });
});

const PORT = process.env.PORT || 3000;
server.listen(PORT, () => {
    console.log(`Servidor WebSocket escuchando en el puerto ${PORT}`);
});
