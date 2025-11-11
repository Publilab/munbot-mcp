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
const WA_DEBOUNCE_MS = parseInt(process.env.WA_DEBOUNCE_MS || '1500', 10);
const waDebounce = new Map(); // from -> {timer, texts: []}

async function processMcpAndRespond(from, userText) {
  const mcpUrl = process.env.MCP_URL || 'http://mcp-core:5000/orchestrate';
  let mcpResp;
  try {
    const stableSid = `+${from}`;
    mcpResp = await axios.post(mcpUrl, {
      pregunta: userText,
      context: { sender: `+${from}` },
      channel: 'whatsapp',
      session_id: stableSid,
    }, { timeout: 20000 });
  } catch (err) {
    console.error('[WA] Error llamando MCP-Core:', err?.response?.data || err.message);
    await sendWhatsAppMessage(from, 'Estamos teniendo problemas para procesar tu mensaje. Intenta nuevamente en unos minutos.');
    return;
  }

  const data = mcpResp?.data || {};
  const outs = Array.isArray(data.respuestas) ? data.respuestas : [];
  const single = data.respuesta || data.message;

  if (outs.length > 0) {
    for (const item of outs) {
      if (item && typeof item === 'object') {
        const txt = item.respuesta || '';
        const sugg = Array.isArray(item.suggested_replies) ? item.suggested_replies : [];
        if (sugg.length > 0) {
          await sendWhatsAppInteractive(from, txt || 'Elige una opción:', sugg);
        } else if (txt) {
          await sendWhatsAppMessage(from, String(txt));
        }
      } else if (item) {
        await sendWhatsAppMessage(from, String(item));
      }
    }
  } else if (single) {
    const sugg = Array.isArray(data.suggested_replies) ? data.suggested_replies : [];
    if (sugg.length > 0) {
      await sendWhatsAppInteractive(from, String(single), sugg);
    } else {
      await sendWhatsAppMessage(from, String(single));
    }
  } else {
    await sendWhatsAppMessage(from, 'No encontré una respuesta para eso. ¿Puedes reformular tu consulta?');
  }
}

// ======================================
// 4. Integración WhatsApp Cloud API (Meta)
// ======================================
// Divide texto largo en partes ≤ maxLen, procurando cortar en saltos de línea o espacios
function splitTextForWhatsApp(text, maxLen = 4000) {
  const t = String(text || '');
  if (t.length <= maxLen) return [t];
  const parts = [];
  let remaining = t;
  const limit = Math.max(1000, Math.min(maxLen, 4000));
  while (remaining.length > limit) {
    // Intentar cortar en salto de línea o espacio cercano al límite
    let cut = remaining.lastIndexOf('\n', limit);
    if (cut < limit * 0.6) {
      const space = remaining.lastIndexOf(' ', limit);
      cut = Math.max(cut, space);
    }
    if (cut <= 0) cut = limit; // corte duro si no hay separador conveniente
    parts.push(remaining.slice(0, cut).trim());
    remaining = remaining.slice(cut).trim();
  }
  if (remaining) parts.push(remaining);
  if (parts.length > 1) {
    return parts.map((p, i) => `${p}\n(${i + 1}/${parts.length})`);
  }
  return parts;
}

async function sendWhatsAppMessage(toNumber, text) {
  const base = (process.env.WHATSAPP_API_URL || 'https://graph.facebook.com/v19.0').replace(/\/$/, '');
  const phoneId = process.env.META_PHONE_ID;
  const token   = process.env.META_TOKEN;

  if (!base || !phoneId || !token) {
    throw new Error('Faltan WHATSAPP_API_URL, META_PHONE_ID o META_TOKEN en .env');
  }

  const url = `${base}/${phoneId}/messages`;
  const segments = splitTextForWhatsApp(text, 4000);
  let last;
  try {
    for (const seg of segments) {
      const payload = {
        messaging_product: 'whatsapp',
        to: normalizeTo(toNumber),       // E.164 sin '+'
        type: 'text',
        text: { body: seg, preview_url: false },
      };
      const r = await axios.post(url, payload, {
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        timeout: 15000,
      });
      last = r.data;
      // Pequeña pausa para mantener orden de lectura cuando hay múltiples partes
      if (segments.length > 1) await new Promise(res => setTimeout(res, 400));
    }
    return last;
  } catch (err) {
    console.error('[WA] Error enviando mensaje:', err?.response?.data || err.message);
    throw err;
  }
}

async function sendWhatsAppInteractive(toNumber, bodyText, options) {
  const base = (process.env.WHATSAPP_API_URL || 'https://graph.facebook.com/v19.0').replace(/\/$/, '');
  const phoneId = process.env.META_PHONE_ID;
  const token   = process.env.META_TOKEN;

  if (!base || !phoneId || !token) {
    throw new Error('Faltan WHATSAPP_API_URL, META_PHONE_ID o META_TOKEN en .env');
  }

  const url = `${base}/${phoneId}/messages`;

  const makeId = (title, i) => String(title).toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, '') || `opt-${i+1}`;
  const optionsRaw = (options || []).slice(0, 10);

  // Usar botones (máx 3, título ≤ 20) o lista (hasta 10, título ≤ 24)
  let payload;
  if (optionsRaw.length > 0 && optionsRaw.length <= 3) {
    const buttonOptions = optionsRaw.map((t, i) => ({ id: makeId(t, i), title: String(t).slice(0, 20) }));
    payload = {
      messaging_product: 'whatsapp',
      to: normalizeTo(toNumber),
      type: 'interactive',
      interactive: {
        type: 'button',
        body: { text: String(bodyText).slice(0, 1024) },
        action: {
          buttons: buttonOptions.map(o => ({ type: 'reply', reply: { id: o.id, title: o.title } }))
        }
      }
    };
  } else {
    const listOptions = optionsRaw.map((t, i) => ({ id: makeId(t, i), title: String(t).slice(0, 24) }));
    payload = {
      messaging_product: 'whatsapp',
      to: normalizeTo(toNumber),
      type: 'interactive',
      interactive: {
        type: 'list',
        header: { type: 'text', text: 'Opciones' },
        body: { text: String(bodyText).slice(0, 1024) },
        action: {
          button: 'Ver opciones',
          sections: [
            {
              title: 'Sugerencias',
              rows: listOptions.map(o => ({ id: o.id, title: o.title }))
            }
          ]
        }
      }
    };
  }

  try {
    const r = await axios.post(url, payload, {
      headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
      timeout: 15000,
    });
    return r.data;
  } catch (err) {
    console.error('[WA] Error enviando interactivo:', err?.response?.data || err.message);
    // Fallback: enviar texto plano si falla el interactivo
    await sendWhatsAppMessage(toNumber, `${bodyText}\n\n${(options||[]).map(o=>`• ${o}`).join('\n')}`);
  }
}

// Mapea IDs canónicos de botones/listas a una frase de entrada estable para el MCP
function mapInteractiveToInput(id, title) {
  if (!id && !title) return '';
  const known = {
    'certificados-y-tramites': '🗂️ Certificados y trámites',
    'agendar-una-cita': '📅 Agendar una cita',
    'presentar-un-reclamo': '📝 Presentar un reclamo',
    'hablar-con-un-agente': '📞 Hablar con un agente',
    'ver-requisitos': 'Ver requisitos',
    'ver-costos': 'Ver costos',
    'ver-horarios': 'Ver horarios',
    'donde-tramitar': 'Dónde tramitar',
    'para-que-sirve': 'Para qué sirve',
  };
  // Preferir id porque el título puede estar truncado por límites de WhatsApp
  // Si tenemos un mapeo conocido, úsalo; si no, usa id completo (slug) y como último recurso el título
  return known[id] || id || title;
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
  const type = msg?.type;            // ej: 'text' | 'interactive'
  let bodyText = '';
  let choiceId = null;
  if (type === 'text') {
    bodyText = msg?.text?.body ?? '';
  } else if (type === 'interactive') {
    const br = msg?.interactive?.button_reply || {};
    const lr = msg?.interactive?.list_reply || {};
    choiceId = br.id || lr.id || null;
    const title = br.title || lr.title || '';
    // Mapear id a frase estable
    bodyText = mapInteractiveToInput(choiceId, title);
  }

  if (!from) return { ok: false, reason: 'missing_from' };
  if (!bodyText) return { ok: false, reason: 'missing_text_body_or_not_text_type', type };

  // 5) normalización
  const normalizedFrom = String(from).replace(/\D/g, '');
  const normalizedText = String(bodyText).trim();

  return {
    ok: true,
    from: normalizedFrom,        // '569XXXXXXXX'
    rawFrom: from,               // por si quieres conservar el original
    text: normalizedText,        // Texto listo para enviar al MCP
    type,
    choiceId,
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
app.get('/health/auth', apiKeyMiddleware, async (req, res) => {
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

  const { from, text: userText, type } = messageData;

  // 3) Debounce/agrupación por número: juntar prefacios + pregunta
  if (type === 'interactive') {
    await processMcpAndRespond(from, userText);
    return;
  }

  const key = from;
  const existing = waDebounce.get(key) || { timer: null, texts: [] };
  existing.texts.push(userText);
  if (existing.texts.length > 3) {
    existing.texts = existing.texts.slice(-3);
  }
  if (existing.timer) clearTimeout(existing.timer);
  existing.timer = setTimeout(async () => {
    const combined = existing.texts.join(' ').trim();
    waDebounce.delete(key);
    await processMcpAndRespond(from, combined);
  }, WA_DEBOUNCE_MS);
  waDebounce.set(key, existing);
});

// ======================================
// 9. Iniciar Servidor
// ======================================
const PORT = process.env.PORT || 8080;
server.listen(PORT, '0.0.0.0', () => {
  console.log(`🚀 Servidor escuchando en puerto ${PORT}`);
});
