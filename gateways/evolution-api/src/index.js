const express = require('express');
const { createServer } = require('http');
const WebSocket = require('ws');
const redis = require('redis');
const fs = require('fs-extra');
const axios = require('axios');
const cors = require('cors');

require('dotenv').config();

// ======================================
// 1. Configuración de Archivo JSON
// ======================================
const HISTORY_FILE = './src/history.json';

// Crear archivo si no existe
fs.ensureFile(HISTORY_FILE)
  .then(() => fs.readJson(HISTORY_FILE).catch(() => []))
  .catch(err => console.error('Error inicializando history.json:', err));

// ======================================
// 2. Conexión a Redis (con reconexión automática y registro de eventos)
// ======================================
let redisClient;

function connectRedis() {
  redisClient = redis.createClient({ url: 'redis://redis:6379' });

  redisClient.on('error', (err) => {
    console.error('🔴 Redis error:', err);
    setTimeout(connectRedis, 5000); // Reconectar cada 5 segundos
  });

  redisClient.on('connect', () => console.log('🟢 Redis conectado'));
  redisClient.connect();
}

connectRedis();

// ======================================
// 3. Servidores WebSocket y Express
// ======================================
const app = express();

const client = require('prom-client');
const collectDefaultMetrics = client.collectDefaultMetrics;
collectDefaultMetrics({ timeout: 5000 });

app.get('/metrics', async (req, res) => {
    res.set('Content-Type', client.register.contentType);
    res.end(await client.register.metrics());
});

// Habilitar CORS para todas las solicitudes
app.use(cors({
  origin: '*', // Cambia esto a la URL de tu frontend en producción
  credentials: true
}));

app.use(express.json()); // Debe ir después de cors

// Endpoint de verificación de webhook para Facebook/Meta
app.get('/webhook/wa', (req, res) => {
  const mode = req.query['hub.mode'];
  const token = req.query['hub.verify_token'];
  const challenge = req.query['hub.challenge'];
  const VERIFY_TOKEN = process.env.META_VERIFY_TOKEN;

  if (!mode || !token) {
    return res.sendStatus(400); // faltan query params
  }

  if (mode === 'subscribe' && token === VERIFY_TOKEN) {
    // OK: Meta valida el webhook devolviendo el challenge tal cual
    console.log('WEBHOOK_VERIFIED');
    return res.status(200).send(challenge);
  }

  // Token inválido o mode inesperado
  return res.sendStatus(403);
});

const server = createServer(app);
const wss = new WebSocket.Server({ server });

// ======================================
// 4. Integración WhatsApp Cloud API (Meta)
// ======================================
async function sendWhatsAppMessage(toNumber, text) {
  const base = (process.env.WHATSAPP_API_URL || 'https://graph.facebook.com/v19.0').replace(/\/$/, '');
  const phoneId = process.env.META_PHONE_ID;
  const token   = process.env.META_TOKEN;

  if (!base || !phoneId || !token) {
    throw new Error('Faltan WHATSAPP_API_URL, META_PHONE_ID o META_TOKEN en .env');
  }

  const url = `${base}/${phoneId}/messages`;
  const payload = {
    messaging_product: 'whatsapp',
    to: normalizeTo(toNumber),       // E.164 sin '+'
    type: 'text',
    text: { body: text },
  };

  try {
    const r = await axios.post(url, payload, {
      headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
      timeout: 15000,
    });
    return r.data;
  } catch (err) {
    console.error('[WA] Error enviando mensaje:', err?.response?.data || err.message);
    throw err;
  }
}

function extractFromAndTextBody(body) {
  // 1) entry
  const entry = Array.isArray(body?.entry) ? body.entry[0] : null;
  if (!entry) return { ok: false, reason: 'missing_entry' };

  // 2) changes
  const change = Array.isArray(entry?.changes) ? entry.changes[0] : null;
  if (!change?.value) return { ok: false, reason: 'missing_change_value' };

  // 3) messages
  const messages = Array.isArray(change.value.messages) ? change.value.messages : [];
  if (messages.length === 0) return { ok: false, reason: 'no_messages' };

  // 4) primer mensaje
  const msg = messages[0];
  const from = msg?.from;            // ej: "569XXXXXXXX"
  const type = msg?.type;            // ej: "text"
  const bodyText = type === 'text' ? (msg?.text?.body ?? '') : '';

  if (!from) return { ok: false, reason: 'missing_from' };
  if (!bodyText) return { ok: false, reason: 'missing_text_body_or_not_text_type', type };

  // 5) normalización
  const normalizedFrom = String(from).replace(/\D/g, '');
  const normalizedText = String(bodyText).trim();

  return {
    ok: true,
    from: normalizedFrom,        // "569XXXXXXXX"
    rawFrom: from,               // por si quieres conservar el original
    text: normalizedText,        // "Hola 👋"
    type
  };
}

function normalizeTo(n) {
  // Convierte "569XXXX..." o "+569XXXX..." a "569XXXX..."
  return String(n).replace(/\D/g, '');
}

// Endpoint para enviar mensaje vía WhatsApp
app.post('/whatsapp/send', async (req, res) => {
  const { phoneNumber, message } = req.body;
  if (!phoneNumber || !message) {
    return res.status(400).json({ error: 'Se requieren phoneNumber y message' });
  }
  try {
    const result = await sendWhatsAppMessage(phoneNumber, message);
    res.json({ success: true, sid: result.sid });
  } catch (error) {
    console.error('Error enviando WhatsApp:', error);
    res.status(500).json({ error: error.message });
  }
});

// ======================================
// Endpoint para Evolution Manager: /instance/fetchInstances
// ======================================
app.get('/instance/fetchInstances', async (req, res) => {
  // Aquí puedes personalizar la lógica para obtener las instancias
  // Por ejemplo, leer de un archivo, base de datos, o devolver un mock
  // Ejemplo de respuesta mock compatible con evolution-manager:
  const instances = [
    {
      id: "default",
      name: "Instancia Principal",
      status: "active",
      description: "Instancia de ejemplo para Evolution Manager"
    }
  ];
  res.json({ success: true, instances });
});

// 5. Manejo y Procesamiento de Mensajes de WhatsApp vía WebSocket
// ======================================
wss.on('connection', (ws, req) => {
  // Se obtiene la IP real del cliente
  const userIp = req.socket.remoteAddress.replace('::ffff:', '');

  ws.on('message', async (data) => {
    try {
      // Parsear el mensaje una sola vez
      const parsedData = JSON.parse(data);
      // Suponemos que el mensaje incluye un campo 'text' y 'number'
      const messageText = parsedData.text || '';
      const phoneNumber = parsedData.number; // Ej: "+56987654321"

      const messageData = {
        number: phoneNumber,
        start_time: new Date().toISOString(),
        messages: [parsedData],
        ip: userIp
      };

      // Guardar el mensaje en history.json
      const history = await fs.readJson(HISTORY_FILE).catch(() => []);
      history.push(messageData);
      await fs.writeJson(HISTORY_FILE, history);

      // Registrar evento de recepción en Redis
      await redisClient.lPush("message_events", JSON.stringify({
        number: phoneNumber,
        timestamp: new Date().toISOString(),
        event: "message_received"
      }));

      // Integración con otros servicios según contenido del mensaje

      // Si el mensaje contiene "reclamo", notificar a Complaints API
      if (messageText.toLowerCase().includes("reclamo")) {
        axios.post(
          process.env.COMPLAINTS_URL || 'http://complaints-api:3001/webhook/new-complaint',
          {
            number: phoneNumber,
            text: messageText,
            timestamp: new Date().toISOString()
          }
        ).catch(err => console.error('Error enviando a Complaints API:', err));
      }

      // La pasarela solo debe reenviar el mensaje al orquestador (MCP),
      // que es el responsable de la lógica de negocio (llamar a RAG, Rasa, etc.).
      let reply;
      try {
        // 1. Corregir URL del MCP: el endpoint es /orchestrate
        const mcpUrl = process.env.MCP_URL || 'http://mcp-core:5000/orchestrate';
        
        // 2. El payload ya está en el formato correcto que espera el orquestador
        const mcpPayload = {
          pregunta: messageText,
          context: { sender: phoneNumber },
          session_id: null, // Aquí podrías manejar un ID de sesión si lo tienes
          channel: 'whatsapp'
        };

        const mcpResponse = await axios.post(mcpUrl, mcpPayload);

        // --- Lógica mejorada para manejar respuestas múltiples o únicas ---
        if (mcpResponse.data && Array.isArray(mcpResponse.data.respuestas)) {
          // Si es una lista, enviar cada mensaje secuencialmente
          for (const msg of mcpResponse.data.respuestas) {
            await sendWhatsAppMessage(phoneNumber, msg);
          }
          // No hay una única respuesta para enviar al final, así que salimos.
          return; 
        } else if (mcpResponse.data && (mcpResponse.data.respuesta || mcpResponse.data.message)) {
          // Si es una respuesta única
          reply = mcpResponse.data.respuesta || mcpResponse.data.message;
        } else {
          // Fallback si el MCP no da una respuesta en el formato esperado
          reply = 'No se recibió una respuesta válida del servicio.';
        }

      } catch (err) {
        console.error('Error al comunicarse con el MCP:', err);
        reply = 'Lo siento, hubo un error procesando tu solicitud.';
      }
      // Enviar la respuesta al usuario vía WebSocket
      ws.send(JSON.stringify({ reply: reply }));

      // Registrar evento de respuesta en Redis
      await redisClient.lPush("message_events", JSON.stringify({
        number: phoneNumber,
        timestamp: new Date().toISOString(),
        event: "message_processed"
      }));

    } catch (error) {
      console.error('💥 Error procesando mensaje:', error);
      ws.send(JSON.stringify({ error: 'Error procesando mensaje' }));
    }
  });
});

// ======================================
// 6. Middleware de autenticación para validar API Key
// ======================================
const apiKeyMiddleware = (req, res, next) => {
  const apiKey = req.headers['apikey'];
  const globalApiKey = process.env.GLOBAL_API_KEY || 'munbot-evolution-api-key-2023';
  
  if (!apiKey || apiKey !== globalApiKey) {
    return res.status(401).json({ error: 'API Key inválida o no proporcionada' });
  }
  next();
};

// ======================================
// 7. Endpoint raíz para validación Evolution Manager
// ======================================
app.get('/', apiKeyMiddleware, (req, res) => {
  res.json({
    message: 'Evolution API',
    version: '1.0.0'
  });
});

// ======================================
// 8. Endpoint de Salud
// ======================================
app.get('/health', (req, res) => {
  res.status(200).json({ status: 'ok', message: 'Evolution API saludable (sin autenticación)' });
});
app.get('/health', apiKeyMiddleware, async (req, res) => {
  let historyCount = 0;
  try {
    const history = await fs.readJson(HISTORY_FILE).catch(() => []);
    historyCount = history.length;
  } catch (err) {
    console.error('Error leyendo history:', err);
  }
  res.json({
    status: 'ok',
    stats: {
      messages: historyCount
    }
  });
});

// --- POST /webhook/wa ---
// Recepción de eventos de WhatsApp Cloud (Meta) y respuesta vía MCP-Core
app.post('/webhook/wa', async (req, res) => {
  // 1) ACK inmediato para que Meta no reintente
  res.sendStatus(200);

  // 2) Extraer y validar datos con el helper
  const messageData = extractFromAndTextBody(req.body);

  if (!messageData.ok) {
    // No logueamos 'no_messages' porque es común (p.ej. notificaciones de status)
    if (messageData.reason !== 'no_messages') {
      console.log(`[WA] Mensaje ignorado: ${messageData.reason}`, { type: messageData.type });
    }
    return;
  }

  const { from, text: userText } = messageData;

  // 3) Llamar al orquestador (MCP-Core)
  const mcpUrl = process.env.MCP_URL || 'http://mcp-core:5000/orchestrate';
  let mcpResp;

  try {
    mcpResp = await axios.post(mcpUrl, {
      pregunta: userText,
      context: { sender: `+${from}` }, // `from` ya está normalizado a solo dígitos
      channel: 'whatsapp',
    }, { timeout: 15000 });
  } catch (err) {
    console.error('[WA] Error llamando MCP-Core:', err?.response?.data || err.message);
    await sendWhatsAppMessage(from, 'Estamos teniendo problemas para procesar tu mensaje. Intenta nuevamente en unos minutos.');
    return;
  }

  // 4) Procesar y enviar respuesta de MCP-Core
  const data = mcpResp?.data || {};
  const outs = Array.isArray(data.respuestas)
    ? data.respuestas
    : [data.respuesta || data.message].filter(Boolean);

  if (outs.length === 0) {
    await sendWhatsAppMessage(from, 'No encontré una respuesta para eso. ¿Puedes reformular tu consulta?');
  } else {
    for (const text of outs) {
      await sendWhatsAppMessage(from, String(text).slice(0, 4000));
    }
  }
});

// ======================================
// 9. Iniciar Servidor
// ======================================
const PORT = process.env.PORT || 8080;
server.listen(PORT, '0.0.0.0', () => {
  console.log(`🚀 Servidor escuchando en puerto ${PORT}`);
});
