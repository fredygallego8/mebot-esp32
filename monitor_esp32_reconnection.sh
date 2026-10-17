#!/bin/bash
echo "🔍 Monitoreando reconexión ESP32..."
echo "Panel admin: http://10.54.66.90:16000/admin"
echo ""

while true; do
    response=$(curl -s http://localhost:16000/stats 2>/dev/null)
    if [ $? -eq 0 ]; then
        sessions=$(echo $response | python3 -c "import sys, json; print(json.load(sys.stdin).get('sessions_active', 0))" 2>/dev/null || echo "0")
        
        if [ "$sessions" -gt 0 ]; then
            echo "🎉 ¡ESP32 RECONECTADO! ($sessions sesiones activas)"
            echo ""
            echo "🧪 Probando comandos automáticamente..."
            
            # Probar TTS
            echo "  📢 Probando TTS..."
            tts_result=$(curl -s -X POST http://localhost:16000/esp32/command -H "Content-Type: application/json" -d '{"command": "test_tts"}')
            if echo "$tts_result" | grep -q "sent to ESP32"; then
                echo "  ✅ Comando TTS enviado correctamente"
            else
                echo "  ❌ Error enviando TTS: $tts_result"
            fi
            
            # Esperar un poco antes del siguiente comando
            sleep 3
            
            # Probar STT
            echo "  🎤 Probando STT..."
            stt_result=$(curl -s -X POST http://localhost:16000/esp32/command -H "Content-Type: application/json" -d '{"command": "test_stt"}')
            if echo "$stt_result" | grep -q "sent to ESP32"; then
                echo "  ✅ Comando STT enviado correctamente"
            else
                echo "  ❌ Error enviando STT: $stt_result"
            fi
            
            echo ""
            echo "🎯 ¡Pruebas completadas! Ahora puedes:"
            echo "• Usar el panel admin para controlar el ESP32"
            echo "• Escuchar respuesta de TTS (si funciona el audio)"
            echo "• Hablar para probar STT → LLM → TTS"
            break
        else
            echo "$(date +%H:%M:%S) ⏳ Esperando reconexión ESP32... (0 sesiones)"
        fi
    else
        echo "$(date +%H:%M:%S) ❌ Error conectando al bridge"
    fi
    sleep 3
done
