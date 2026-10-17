const https = require('https');
const http = require('http');
const WebSocket = require('ws');
const { exec } = require('child_process');

const CF_TOKEN = process.env.CF_GATEWAY_TOKEN || process.env.cf_aig;
const TG_TOKEN = process.env.TELEGRAM_BOT_TOKEN;
if (!CF_TOKEN) { console.error('[bridge] ERROR: CF_GATEWAY_TOKEN not set'); process.exit(1); }
if (!TG_TOKEN) { console.error('[bridge] ERROR: TELEGRAM_BOT_TOKEN not set'); process.exit(1); }
const MODEL = process.env.PRIMARY_MODEL ? process.env.PRIMARY_MODEL.replace('openrouter/', '') : 'google/gemini-2.5-flash-lite';
const CF_URL = process.env.LLM_URL ? process.env.LLM_URL + '/v1/chat/completions' : 'https://gateway.ai.cloudflare.com/v1/bd69a6636f91b1c0fe511aa72446bb52/poc-mebot/openrouter/v1/chat/completions';
let offset = 0;
const history = {};
const MAX_HISTORY = 20;
const CLI = process.env.MEBOT_CLI_PATH || 'node /home/openclaw/global-dashboard/maintenance-cli.js';
const STACKS = ['mebot', 'ferreteria', 'weiqi'];
const ESP32_CLIENTS = new Map();
try {
  const ESP32_WS_PORT = parseInt(process.env.ESP32_WS_PORT || '18082', 10);
  new WebSocket.Server({ port: ESP32_WS_PORT }).on('connection', (ws, req) => {
    const id = req?.headers?.['x-device-id'] || 'esp32_' + Date.now();
    ESP32_CLIENTS.set(id, ws);
    console.log(`[esp32] device connected: ${id}`);
    ws.on('close', () => { ESP32_CLIENTS.delete(id); console.log(`[esp32] device disconnected: ${id}`); });
    ws.on('error', () => ESP32_CLIENTS.delete(id));
  });
  console.log(`[esp32] WS server on :${ESP32_WS_PORT}`);
} catch(e) {
  console.log(`[esp32] WS server skipped: ${e.message}`);
}

function now() {
  return new Date().toLocaleString('es-CO', { timeZone: 'America/Bogota', dateStyle: 'full', timeStyle: 'short' });
}

function api(method, data) {
  return new Promise((resolve, reject) => {
    const body = data ? JSON.stringify(data) : '';
    const u = new URL(`https://api.telegram.org/bot${TG_TOKEN}/${method}${data ? '' : '?offset=' + (offset + 1) + '&timeout=10'}`);
    const opts = { method: 'POST', hostname: u.hostname, path: u.pathname + u.search, headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(body) }, timeout: 15000 };
    const req = https.request(opts, res => {
      let d = ''; res.on('data', c => d += c); res.on('end', () => { try { resolve(JSON.parse(d)); } catch(e) { reject(e); } });
    });
    req.on('error', reject);
    req.on('timeout', () => { req.destroy(); reject(new Error('timeout')); });
    req.write(body);
    req.end();
  });
}

function cfChat(messages) {
  return new Promise((resolve, reject) => {
    const body = JSON.stringify({ model: MODEL, messages, max_tokens: 200 });
    const u = new URL(CF_URL);
    const opts = { method: 'POST', hostname: u.hostname, path: u.pathname, headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(body), 'cf-aig-authorization': `Bearer ${CF_TOKEN}` }, timeout: 30000 };
    const req = https.request(opts, res => {
      let d = ''; res.on('data', c => d += c); res.on('end', () => { try { const j = JSON.parse(d); resolve(j.choices?.[0]?.message?.content || '[sin respuesta]'); } catch(e) { reject(e); } });
    });
    req.on('error', reject);
    req.on('timeout', () => { req.destroy(); reject(new Error('timeout')); });
    req.write(body);
    req.end();
  });
}

function runCmd(cmd) {
  return new Promise(resolve => {
    exec(cmd, { timeout: 15000 }, (err, stdout) => {
      if (err) return resolve(null);
      resolve(stdout.trim().slice(0, 800));
    });
  });
}

function runGraphQL(query) {
  return runCmd(`${CLI} graphql "${query.replace(/"/g, '\\"')}"`);
}

function detectIntent(text) {
  const t = text.toLowerCase();
  if (/listar|cuántas|cuantas|cantidad|activas?|pendientes?|órdenes|ordenes/i.test(t))
    return { action: 'list' };
  if (/crea?r?|nueva?|registr?a?r?|abrir/i.test(t) && /órden|orden|mantenimient|ticket/i.test(t))
    return { action: 'create', text: t };
  if (/aprob?a?r?|aproba?r?|asign?a?r?|tomar/i.test(t) && /(\d{3,})/i.test(t))
    return { action: 'approve', id: t.match(/(\d{3,})/)[1] };
  if (/nota|comentari|anotar|observación/i.test(t) && /(\d{3,})/i.test(t))
    return { action: 'note', text: t };
  if (/cerra?r?|complet?a?r?|finaliza?r?|termina?r?/i.test(t) && /(\d{3,})/i.test(t))
    return { action: 'complete', id: t.match(/(\d{3,})/)[1] };
  if (/cierre|cerrada|ayer|fecha|este mes|este año/i.test(t) && /órden|orden/i.test(t))
    return { action: 'list-query', text: t };
  if (/clientes|customers|usuarios/i.test(t) && !/órden|orden/i.test(t))
    return { action: 'customers', text: t };
  if (/producto|articulo|ítem|item|inventario|catálogo|catalogo|qué hay/i.test(t) && !/órden|orden/i.test(t))
    return { action: 'products', text: t };
  if (/colección|coleccion|categoría|categoria|grupo|facet/i.test(t) && !/producto/i.test(t))
    return { action: 'collections', text: t };
  if (/variante/i.test(t) && /producto|articulo/i.test(t))
    return { action: 'variants', text: t };
  if (/ventas|canal|channel|moneda|método.*pago|shipping/i.test(t) && !/órden|orden/i.test(t))
    return { action: 'config', text: t };
  if (/busca|encuentra|search|cómo.*se.*llama|existe.*producto/i.test(t))
    return { action: 'search', text: t };
  if (/por stack|por stack|ferretería|ferreteria|weiqi|mebot|mkall/i.test(t) && /órden|orden/i.test(t))
    return { action: 'list-stack', text: t };
  if (/esp32|relay|rele|pantalla|display|enciende|apaga|di.*hola|muestra.*pantalla|muestra.*lcd|speak|tts|voz/i.test(t))
    return { action: 'esp32', text: t };
  return null;
}

async function runMaintenance(action, text) {
  switch (action) {
    case 'list':
      return runCmd(`${CLI} list`);
    case 'list-query': {
      if (/ayer/i.test(text)) {
        const d = new Date(); d.setDate(d.getDate() - 1);
        const date = d.toISOString().slice(0, 10);
        return runGraphQL(`query{orders(options:{filter:{updatedAt:{after:"${date}T00:00:00.000Z"}},take:20}){items{id code state updatedAt customer{emailAddress}}}}`);
      }
      if (/este mes/i.test(text)) {
        const date = new Date().toISOString().slice(0, 7);
        return runGraphQL(`query{orders(options:{filter:{createdAt:{after:"${date}-01T00:00:00.000Z"}},take:30}){items{id code state createdAt customer{emailAddress}}}}`);
      }
      return runCmd(`${CLI} list`);
    }
    case 'customers':
      return runGraphQL('query{customers(options:{take:20}){items{id firstName lastName emailAddress createdAt}}}');
    case 'products':
      return runGraphQL('query{products(options:{take:15}){items{id name slug description}}}');
    case 'collections':
      return runGraphQL('query{collections(options:{take:10}){items{id name slug description}}}');
    case 'variants':
      return runGraphQL('query{productVariants(options:{take:15}){items{id name sku price priceWithTax stockOnHand}}}');
    case 'config':
      return runGraphQL('query{channels{items{id code token pricesIncludeTax defaultLanguage}}shippingMethods{items{id code name}}paymentMethods{items{id code name}}}');
    case 'search': {
      const q = text.replace(/busca|encuentra|search|qué|como|cómo|se.*llama|existe|producto/gi, '').trim().slice(0, 50);
      return q ? runGraphQL(`query{search(input:{term:"${q}",take:5}){items{productName description slug}}}`) : runGraphQL('query{products(options:{take:10}){items{id name slug}}}');
    }
    case 'list-stack': {
      const stack = STACKS.find(s => text.includes(s)) || 'mebot';
      return runGraphQL(`query{orders(options:{take:20}){items{id code state createdAt customer{emailAddress}}}}`);
    }
    case 'create': {
      const stack = STACKS.find(s => text.includes(s)) || 'mebot';
      const title = text.replace(/crea?r?|nueva?|registr?a?r?|abrir|órden|orden|mantenimient|ticket|para|stack|en\s+/gi, '').trim().slice(0, 80);
      return runCmd(`${CLI} create ${stack} "${title}" "Creada desde Telegram por el usuario"`);
    }
    case 'approve':
      return runCmd(`${CLI} approve ${text.match(/(\d{3,})/)[1]}`);
    case 'note': {
      const id = text.match(/(\d{3,})/)[1];
      const note = text.replace(/nota|comentari|anotar|observación|\d{3,}/gi, '').trim().slice(0, 200);
      return runCmd(`echo "${note}" | ${CLI} note ${id}`);
    }
    case 'complete':
      return runCmd(`${CLI} complete ${text.match(/(\d{3,})/)[1]}`);
    case 'esp32': {
      const t = text.toLowerCase();
      let cmd = {};
      if (/enciende|activa.*relay|relay.*on|toggle.*on/i.test(t)) cmd = { action: 'toggle_relay', state: true };
      else if (/apaga|desactiva.*relay|relay.*off|toggle.*off/i.test(t)) cmd = { action: 'toggle_relay', state: false };
      else if (/di.*hola|saluda|say.*hello/i.test(t)) cmd = { action: 'say_hello' };
      else if (/speak|voz|di\s/i.test(t)) cmd = { action: 'speak', text: t.replace(/.*(speak|voz|di)\s+/i, '').trim().slice(0, 100) };
      else if (/muestra|pantalla|display|lcd/i.test(t)) cmd = { action: 'display_msg', message: t.replace(/.*(muestra|pantalla|display|lcd)\s+/i, '').trim().slice(0, 50) };
      else cmd = { action: 'ping' };
      const json = JSON.stringify(cmd);
      ESP32_CLIENTS.forEach(ws => { try { ws.send(json); } catch(e) {} });
      const count = ESP32_CLIENTS.size;
      return Promise.resolve(count > 0 ? `Comando ${cmd.action} enviado a ${count} ESP32` : 'No hay ESP32 conectados');
    }
    default:
      return Promise.resolve(null);
  }
}

async function poll() {
  try {
    const updates = await api('getUpdates');
    if (!updates.ok || !updates.result) return;
    for (const upd of updates.result) {
      if (upd.update_id <= offset) continue;
      offset = upd.update_id;
      const msg = upd.message;
      if (!msg || !msg.text || msg.from?.is_bot) continue;
      const chatId = msg.chat.id;
      console.log(`[bridge] << ${chatId}: "${msg.text.slice(0,60)}"`);
      const typingInt = setInterval(() => api('sendChatAction', { chat_id: chatId, action: 'typing' }).catch(() => {}), 4000);
      try {
        if (!history[chatId]) history[chatId] = [];
        history[chatId].push({ role: 'user', content: msg.text });
        if (history[chatId].length > MAX_HISTORY) history[chatId] = history[chatId].slice(-MAX_HISTORY);

        const ph = await api('sendMessage', { chat_id: chatId, text: '⏳ Procesando...' });
        const intent = detectIntent(msg.text);
        let reply;

        if (intent) {
          console.log(`[bridge] intent ${intent.action} for ${chatId}`);
          const data = await runMaintenance(intent.action, msg.text);
          if (data) console.log(`[bridge] result: ${data.length} chars`);
          const answer = data ? `${data}` : '';
          const msgs = [{ role: 'system', content: `Eres MeBot. Responde en español Colombia (paisa). Máximo 2-3 frases. Hoy es ${now()}.${answer ? '\n\nDatos del sistema:\n' + answer : ''}` }, { role: 'user', content: msg.text }];
          reply = await cfChat(msgs);
        }

        if (!reply) {
          const msgs = [{ role: 'system', content: `Eres MeBot, asistente conciso. Responde en español Colombia (paisa). Máximo 2-3 frases. Hoy es ${now()}.` }, ...(history[chatId] || []), { role: 'user', content: msg.text }];
          reply = await cfChat(msgs);
        }
        if (!reply) reply = 'Listo.';
        history[chatId].push({ role: 'assistant', content: reply });
        if (history[chatId].length > MAX_HISTORY) history[chatId] = history[chatId].slice(-MAX_HISTORY);

        clearInterval(typingInt);
        if (ph.ok && ph.result) {
          await api('editMessageText', { chat_id: chatId, message_id: ph.result.message_id, text: reply });
        } else {
          await api('sendMessage', { chat_id: chatId, text: reply });
        }
        console.log(`[bridge] >> ${chatId}: "${reply.slice(0,60)}..."`);
      } catch (e) {
        clearInterval(typingInt);
        console.error(`[bridge] LLM error: ${e.message}`);
        try { await api('editMessageText', { chat_id: chatId, message_id: ph?.result?.message_id, text: '⚠️ Error interno. Intenta de nuevo.' }); } catch(e2) {
          await api('sendMessage', { chat_id: chatId, text: '⚠️ Error interno. Intenta de nuevo.' });
        }
      }
    }
  } catch (e) {
    if (e.message !== 'timeout') console.error(`[bridge] poll error: ${e.message}`);
  }
}

async function processMessage(text) {
  const intent = detectIntent(text);
  let reply;
  if (intent) {
    const data = await runMaintenance(intent.action, text);
    const answer = data ? `${data}` : '';
    reply = await cfChat([{ role: 'system', content: `Eres MeBot. Responde en español Colombia (paisa). Máximo 2-3 frases. Hoy es ${now()}.${answer ? '\n\nDatos:\n' + answer : ''}` }, { role: 'user', content: text }]);
  }
  if (!reply) {
    reply = await cfChat([{ role: 'system', content: `Eres MeBot, asistente conciso. Responde en español Colombia (paisa). Máximo 2-3 frases. Hoy es ${now()}.` }, { role: 'user', content: text }]);
  }
  return reply || 'Listo.';
}

const HTTP_PORT = 18080;
const server = http.createServer(async (req, res) => {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
  if (req.method === 'OPTIONS') { res.writeHead(204); res.end(); return; }

  // Nuevo endpoint: GET /api/cli?action=...&text=... → ejecuta comandos ERP
  if (req.url.startsWith('/api/cli')) {
    const u = new URL(req.url, 'http://localhost');
    const action = u.searchParams.get('action') || 'list';
    const text = u.searchParams.get('text') || '';
    try {
      const data = await runMaintenance(action, text);
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true, data: data || '' }));
    } catch (e) {
      res.writeHead(500);
      res.end(JSON.stringify({ ok: false, error: e.message }));
    }
    return;
  }

  if (req.method !== 'POST' || req.url !== '/api/chat') {
    res.writeHead(404); res.end(JSON.stringify({ error: 'not found' })); return;
  }
  let body = '';
  req.on('data', c => body += c);
  req.on('end', async () => {
    try {
      const { message } = JSON.parse(body);
      if (!message) { res.writeHead(400); res.end(JSON.stringify({ error: 'message required' })); return; }
      const reply = await processMessage(message);
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ reply }));
    } catch (e) {
      res.writeHead(500); res.end(JSON.stringify({ error: e.message }));
    }
  });
});
server.listen(HTTP_PORT, () => console.log(`[bridge] HTTP API on :${HTTP_PORT}`));

console.log(`[bridge] Telegram Bridge started. Model: ${MODEL}`);
setInterval(poll, 3000);
poll();