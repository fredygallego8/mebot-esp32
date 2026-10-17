#!/bin/bash
echo "🔌 Guía para conectar ESP32 a MeBot Bridge"
echo "=========================================="
echo ""
echo "📋 Checklist de conexión:"
echo "✅ Bridge corriendo en http://10.54.66.90:16000"
echo "✅ OTA endpoint listo: $(curl -s http://10.54.66.90:16000/xiaozhi/ota/ | python3 -c "import sys,json; print('✅' if 'websocket' in json.load(sys.stdin) else '❌')")"
echo ""
echo "🎯 Pasos para conectar ESP32:"
echo ""
echo "1️⃣  VERIFICA RED WIFI:"
echo "   - ESP32 debe estar en red 10.54.66.x"
echo "   - IP del bridge: 10.54.66.90"
echo ""
echo "2️⃣  REINICIA ESP32:"
echo "   - Desconecta alimentación completamente"
echo "   - Espera 10 segundos"
echo "   - Reconecta alimentación"
echo ""
echo "3️⃣  ESPERA CONEXIÓN:"
echo "   - ESP32 se conecta a WiFi"
echo "   - Hace petición OTA automáticamente"
echo "   - Recibe URL: ws://10.54.66.90:16000/"
echo "   - Establece WebSocket connection"
echo ""
echo "4️⃣  VERIFICA EN PANEL:"
echo "   - Ve a: http://10.54.66.90:16000/admin"
echo "   - Estado ESP32 debería cambiar a 'Conectado'"
echo "   - Los botones de control funcionarán"
echo ""
echo "🔍 Monitoreando conexiones..."
echo ""
while true; do
    response=$(curl -s http://localhost:16000/stats 2>/dev/null)
    if [ $? -eq 0 ]; then
        sessions=$(echo $response | python3 -c "import sys, json; print(json.load(sys.stdin).get('sessions_active', 0))" 2>/dev/null || echo "0")
        if [ "$sessions" -gt 0 ]; then
            echo "🎉 ¡ESP32 CONECTADO! ($sessions sesiones activas)"
            echo "🌐 Panel admin: http://10.54.66.90:16000/admin"
            echo "🎛️ Botones de control ESP32 ahora activos"
            break
        else
            echo "$(date +%H:%M:%S) ⏳ Esperando conexión ESP32... (0 sesiones)"
        fi
    else
        echo "$(date +%H:%M:%S) ❌ Error conectando al bridge"
    fi
    sleep 3
done
