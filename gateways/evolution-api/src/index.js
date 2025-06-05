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

  // El token que configuraste en Facebook Developers
  const VERIFY_TOKEN = process.env.META_VERIFY_TOKEN;

  if (mode && token) {
    if (mode === 'subscribe' && token === VERIFY_TOKEN) {
      // Token válido, responde con el challenge
      console.log('WEBHOOK_VERIFIED');
      res.status(200).send(challenge);
    } else {
      // Token inválido
      res.sendStatus(403);
    }
  } else {
    res.sendStatus(400);
  }
});

const server = createServer(app);
const wss = new WebSocket.Server({ server });

// ======================================
// 4. Integración WhatsApp Cloud API (Meta)
// ======================================
async function sendWhatsAppMessage(phoneNumber, message) {
  if (!process.env.META_PHONE_ID || !process.env.META_TOKEN) {
    throw new Error("Faltan credenciales de WhatsApp Cloud API");
  }
  const url = `https://graph.facebook.com/v19.0/${process.env.META_PHONE_ID}/messages`;
  const payload = {
    messaging_product: "whatsapp",
    to: phoneNumber.replace(/\D/g, ''),  // Meta exige dígitos, ej. 56998765432
    type: "text",
    text: { body: message }
  };
  return axios.post(url, payload, {
    headers: { Authorization: `Bearer ${process.env.META_TOKEN}` }
  });
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

      // Enviar mensaje a Rasa
      let reply;
      try {
        const rasaResponse = await axios.post(
          process.env.RASA_URL || 'http://localhost:5005/webhooks/evolution',
          { message: parsedData }
        );
        reply = rasaResponse.data.reply;
      } catch (err) {
        console.error('Error con Rasa:', err);
      }

      // Si Rasa no responde o no se obtiene reply, usar LLM Gateway como fallback
      if (!reply) {
        try {
          const llmResponse = await axios.post(
            process.env.LLM_URL || 'http://llm-gateway:8000/process',
            { message: parsedData }
          );
          reply = llmResponse.data.reply;
        } catch (err) {
          console.error('Error con LLM Gateway:', err);
          reply = "Lo siento, ha ocurrido un error al procesar tu mensaje.";
        }
      }

      // Enviar mensaje al MCP
      try {
        const mcpUrl = process.env.MCP_URL || 'http://mcp-core:000/route';
        const mcpPayload = {
          sender: phoneNumber,
          message: messageText
        };
        const mcpResponse = await axios.post(mcpUrl, mcpPayload);
        if (mcpResponse.data && mcpResponse.data.respuesta) {
          reply = mcpResponse.data.respuesta;
        } else if (mcpResponse.data && mcpResponse.data.message) {
          reply = mcpResponse.data.message;
        } else {
          reply = 'No se recibió respuesta válida del MCP.';
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

// Webhook Cloud API (POST)
app.post('/webhook/wa', (req, res) => {
  console.log('>> Webhook recibido:', JSON.stringify(req.body, null, 2));
  const entry = req.body.entry?.[0];
  const change = entry?.changes?.[0];
  const msg = change?.value?.messages?.[0];
  if (msg && msg.from && msg.text?.body) {
    // reenviamos al WebSocket para que Rasa procese
    wss.clients.forEach(c =>
      c.send(JSON.stringify({ number: `+${msg.from}`, text: msg.text.body }))
    );
  }
  res.sendStatus(200);
});

// ======================================
// 9. Iniciar Servidor
// ======================================
const PORT = process.env.PORT || 8080;
server.listen(PORT, '0.0.0.0', () => {
  console.log(`🚀 Servidor escuchando en puerto ${PORT}`);
});
