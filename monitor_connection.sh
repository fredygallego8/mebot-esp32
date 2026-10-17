#!/bin/bash
echo "🔍 Monitoreando conexiones ESP32..."
echo "Presiona Ctrl+C para detener"
echo ""

while true; do
    response=$(curl -s http://localhost:16000/stats)
    if [ $? -eq 0 ]; then
        sessions=$(echo $response | python3 -c "import sys, json; print(json.load(sys.stdin).get('sessions_active', 0))" 2>/dev/null)
        if [ "$sessions" -gt 0 ]; then
            echo "🎉 ¡ESP32 CONECTADO! Sesiones activas: $sessions"
            echo "🌐 Panel admin: http://10.54.66.90:16000/admin"
            echo "🎛️ Los botones de control ahora funcionan"
            break
        else
            echo "$(date +%H:%M:%S) - Esperando conexión ESP32... ($sessions sesiones)"
        fi
    else
        echo "$(date +%H:%M:%S) - Error conectando al bridge"
    fi
    sleep 2
done
