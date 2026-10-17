#!/usr/bin/env bash
# launch_chrome_cdp.sh
# Mata todas las instancias de Chrome y lo vuelve a abrir con el puerto de depuración 9222.

echo "=== INICIANDO CHROME CON PROTOCOLO CDP ==="
echo "1. Cerrando procesos residuales de Google Chrome..."
killall -9 chrome google-chrome google-chrome-stable 2>/dev/null || true
sleep 1

echo "2. Lanzando Google Chrome con --remote-debugging-port=9222..."
# Lanzar usando el perfil real del usuario para heredar sesiones
google-chrome --remote-debugging-port=9222 > /dev/null 2>&1 &

echo "3. ¡Listo! Espera unos segundos a que cargue Chrome y luego ejecuta:"
echo "   node qa/web_flash_test.js --cdp"
echo "=========================================="
