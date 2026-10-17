/**
 * mock_bridge.js
 * ─────────────────────────────────────────────────────────────
 * Servidor HTTP mínimo para pruebas locales del Flasheador Web.
 * Sirve el manifest.json en el endpoint compatible con el bridge
 * de producción (bot2market.mebot.online) sin necesitar Python.
 *
 * Uso standalone:
 *   node qa/mock_bridge.js
 *
 * Uso desde web_flash_test.js:
 *   const { createMockBridge, MOCK_PORT } = require('./mock_bridge');
 *   const server = await createMockBridge();
 *   // ... test ...
 *   server.close();
 */

'use strict';

const http = require('http');

const MOCK_PORT = parseInt(process.env.MOCK_PORT || '16099');

const MANIFEST = {
  name: 'MeBot AI CoreS3',
  version: '2.8.1-TEST',
  new_install_prompt_erase: true,
  builds: [
    {
      chipFamily: 'ESP32-S3',
      parts: [
        { path: 'bootloader.bin',       offset: 0       },
        { path: 'partition-table.bin',  offset: 32768   },
        { path: 'ota_data_initial.bin', offset: 53248   },
        { path: 'mebot.bin',            offset: 131072  },
        { path: 'generated_assets.bin', offset: 8388608 },
      ],
    },
  ],
};

/**
 * Inicia el servidor mock. Retorna una promesa que resuelve con el
 * objeto `server` de Node.js listo para escuchar peticiones.
 */
function createMockBridge(port = MOCK_PORT) {
  const server = http.createServer((req, res) => {
    // CORS: necesario porque la página está en https://mebot.online
    res.setHeader('Access-Control-Allow-Origin', '*');
    res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
    res.setHeader('Access-Control-Allow-Headers', '*');
    res.setHeader('Cache-Control', 'no-cache');

    if (req.method === 'OPTIONS') {
      res.writeHead(204);
      res.end();
      return;
    }

    const url = req.url.split('?')[0]; // ignorar query strings

    if (url === '/esp32/ota/manifest.json') {
      const body = JSON.stringify(MANIFEST);
      res.writeHead(200, {
        'Content-Type': 'application/json',
        'Content-Length': Buffer.byteLength(body),
      });
      res.end(body);
      return;
    }

    if (url === '/health') {
      const body = JSON.stringify({ status: 'ok', mode: 'mock-test', port });
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(body);
      return;
    }

    // Cualquier .bin solicitado por esp-web-tools → cuerpo vacío (stub)
    if (url.endsWith('.bin')) {
      res.writeHead(200, {
        'Content-Type': 'application/octet-stream',
        'Content-Length': '0',
      });
      res.end();
      return;
    }

    res.writeHead(404, { 'Content-Type': 'text/plain' });
    res.end('Not found');
  });

  return new Promise((resolve, reject) => {
    server.on('error', reject);
    server.listen(port, '127.0.0.1', () => {
      console.log(`[MockBridge] ✅ Servidor de prueba activo → http://127.0.0.1:${port}`);
      resolve(server);
    });
  });
}

// ── Ejecución directa ──────────────────────────────────────────────────────
if (require.main === module) {
  createMockBridge()
    .then(() => {
      console.log('[MockBridge] Presiona Ctrl+C para detener.');
    })
    .catch((e) => {
      console.error('[MockBridge] Error al iniciar:', e.message);
      process.exit(1);
    });
}

module.exports = { createMockBridge, MOCK_PORT };
