// web_app/socketServer.js

require('dotenv').config();

const express = require('express');
const http = require('http');
const { Server } = require('socket.io');
const axios = require('axios');
const path = require('path');

const app = express();

// Setup prom-client
const client = require('prom-client');
const collectDefaultMetrics = client.collectDefaultMetrics;
collectDefaultMetrics({ timeout: 5000 });

app.get('/metrics', async (req, res) => {
    res.set('Content-Type', client.register.contentType);
    res.end(await client.register.metrics());
});

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

const MCP_URL = process.env.MCP_URL || 'http://mcp-core:5000/orchestrate';
// Aumentamos el timeout a 50 segundos para dar margen al orquestador (que tiene 30s)
const MCP_TIMEOUT = parseInt(process.env.MCP_TIMEOUT || '50000', 10);
// Debounce para juntar mensajes seguidos (p. ej., "Quiero hacer una pregunta" + la pregunta)
const WEB_DEBOUNCE_MS = parseInt(process.env.WEB_DEBOUNCE_MS || '1500', 10);
const WEB_DEBOUNCE_EXT_MS = parseInt(process.env.WEB_DEBOUNCE_EXT_MS || '2400', 10);
const WEB_SHORT_MSG_THRESHOLD = parseInt(process.env.WEB_SHORT_MSG_THRESHOLD || '80', 10);
const CONNECTOR_WORDS = (process.env.WEB_CONNECTOR_WORDS || 'sobre,para,y').split(',').map(w => w.trim()).filter(Boolean);
const CONNECTOR_REGEX = CONNECTOR_WORDS.length ? new RegExp(`\\b(${CONNECTOR_WORDS.join('|')})$`, 'i') : null;

async function postWithRetry(payload, attempts = 3, delay = 1000) {
    let lastError;
    for (let i = 0; i < attempts; i++) {
        try {
            console.log(`Intentando conectar con MCP (Intento ${i + 1}/${attempts})...`);
            return await axios.post(MCP_URL, payload, { timeout: MCP_TIMEOUT });
        } catch (err) {
            console.error(`Error en intento ${i + 1}:`, err.code);
            lastError = err;
            if (i < attempts - 1) {
                await new Promise(res => setTimeout(res, delay));
                delay *= 2;
            }
        }
    }
    throw lastError;
}

io.on('connection', (socket) => {
    console.log('Un usuario se ha conectado');
    // Inicializar session_id por socket
    socket.sessionId = null;
    // Estado de debounce por socket
    socket._debounceTimer = null;
    socket._buffer = [];
    socket._pendingDebounceMs = WEB_DEBOUNCE_MS;

    async function sendToMCP(text) {
        // Construir el payload para el MCP. La session_id debe enviarse en la
        // raiz del JSON para que el orquestador pueda recuperarla.
        const payload = {
            pregunta: text,
            context: { sender: socket.id },
            session_id: socket.sessionId, // USAR sessionId del socket
            channel: 'web'
        };
        // Enviar el mensaje al MCP con reintentos
        const response = await postWithRetry(payload);
        // Actualizar el identificador de sesion si es devuelto por el MCP
        if (response.data) {
            socket.sessionId = response.data.session_id || socket.sessionId;
        }
        const data = response.data || {};
        const hasReplies = Array.isArray(data.suggested_replies) && data.suggested_replies.length > 0;
        const messageText = data.respuesta || data.message || 'No se recibió respuesta válida del MCP.';

        if (Array.isArray(data.respuestas)) {
            // Manejar una lista de respuestas de cualquier longitud
            data.respuestas.forEach((botMsg, index) => {
                setTimeout(() => {
                    if (typeof botMsg === 'object' && botMsg !== null && botMsg.suggested_replies) {
                        socket.emit('bot_payload', botMsg);
                    } else if (typeof botMsg === 'object' && botMsg !== null && botMsg.respuesta) {
                        socket.emit('bot_message', botMsg.respuesta);
                    } else {
                        socket.emit('bot_message', botMsg);
                    }
                }, index * 1200); // Pausa de 1.2 segundos entre mensajes
            });
        } else if (hasReplies) {
            socket.emit('bot_payload', {
                respuesta: messageText,
                suggested_replies: data.suggested_replies
            });
        } else {
            socket.emit('bot_message', messageText);
        }
    }

    function needsExtendedDelay(text) {
        const trimmed = String(text || '').trim().toLowerCase();
        if (!trimmed) return false;
        const isShort = trimmed.length <= WEB_SHORT_MSG_THRESHOLD;
        const endsConnector = CONNECTOR_REGEX ? CONNECTOR_REGEX.test(trimmed) : false;
        return isShort || endsConnector;
    }

    async function flushBuffer() {
        const combined = socket._buffer.join(' ').trim();
        socket._buffer = [];
        socket._debounceTimer = null;
        socket._pendingDebounceMs = WEB_DEBOUNCE_MS;
        if (!combined) {
            return;
        }
        await sendToMCP(combined);
    }

    function scheduleDebounce(delay) {
        if (socket._debounceTimer) {
            clearTimeout(socket._debounceTimer);
        }
        socket._pendingDebounceMs = delay;
        socket._debounceTimer = setTimeout(async () => {
            try {
                await flushBuffer();
            } catch (err) {
                console.error('Error al enviar buffer:', err);
                socket.emit('bot_message', 'Lo siento, hubo un error procesando tu solicitud.');
            }
        }, delay);
    }

    socket.on('message', async (msg) => {
        console.log('Mensaje recibido del cliente:', msg);
        try {
            socket._buffer.push(String(msg));
            const delay = needsExtendedDelay(msg) ? WEB_DEBOUNCE_EXT_MS : WEB_DEBOUNCE_MS;
            scheduleDebounce(delay);
        } catch (error) {
            console.error('Error al comunicarse con el MCP:', error);
            socket.emit('bot_message', 'Lo siento, hubo un error procesando tu solicitud.');
        }
    });

    socket.on('typing', (payload = {}) => {
        const { hasText } = payload;
        if (hasText && socket._debounceTimer) {
            const nextDelay = socket._pendingDebounceMs || WEB_DEBOUNCE_MS;
            scheduleDebounce(nextDelay);
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
