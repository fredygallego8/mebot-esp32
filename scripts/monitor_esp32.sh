#!/usr/bin/env bash
# monitor_esp32.sh — Monitor dual en tiempo real
# Muestra en columnas paralelas: ESP32 serial + logs del bridge
# Uso: ./monitor_esp32.sh [segundos]
# Ctrl+C para terminar antes

set -euo pipefail

DURATION="${1:-60}"
ESP_PORT="${ESP_PORT:-/dev/ttyACM0}"
BRIDGE_LOG="${BRIDGE_LOG:-/tmp/mebot_bridge.log}"
BAUD="${BAUD:-115200}"

# Colores
COLOR_ESP='\033[36m'      # Cyan
COLOR_BRIDGE='\033[32m'   # Verde
COLOR_WARN='\033[33m'     # Amarillo
COLOR_ERR='\033[31m'      # Rojo
COLOR_RESET='\033[0m'
COLOR_BOLD='\033[1m'

cleanup() {
    kill $PID_ESP $PID_BRIDGE 2>/dev/null
    wait $PID_ESP $PID_BRIDGE 2>/dev/null
    echo ""
    echo -e "${COLOR_BOLD}Monitor terminado (${DURATION}s)${COLOR_RESET}"
}

trap cleanup EXIT INT TERM

echo -e "${COLOR_BOLD}╔══════════════════════════════════════════════════════════╗${COLOR_RESET}"
echo -e "${COLOR_BOLD}║  ESP32 Monitor — ${DURATION}s                                  ║${COLOR_RESET}"
echo -e "${COLOR_BOLD}╠══════════════════════════════════════════════════════════╣${COLOR_RESET}"
echo -e "${COLOR_BOLD}║  ESP32: ${ESP_PORT} | Bridge: localhost:16000${COLOR_RESET}"
echo -e "${COLOR_BOLD}║  Leyendo... presiona el boton del CoreS3 ahora.${COLOR_RESET}"
echo -e "${COLOR_BOLD}╚══════════════════════════════════════════════════════════╝${COLOR_RESET}"
echo ""

# ESP32 serial reader
python3 -c "
import serial, time, sys, re

ser = serial.Serial('${ESP_PORT}', ${BAUD}, timeout=0.5)
t0 = time.time()
end = t0 + ${DURATION}

KEY_EVENTS = ('Estado','SPEAKING','LISTENING','VAD:','wifi:','Connected','Ota:','HttpClient',
              'RemoteConfig','IDLE','Audio:','AudioCodec','CoreS3Audio','Adev_Codec','opus',
              'Output volume','Volume','Error','Toggle','Ws','ws','WebSocket','Hello','hello',
              'MCP: Add tool','Establish','10.119.253','free sram')

while time.time() < end:
    try:
        line = ser.readline()
        if not line:
            continue
        s = line.decode(errors='replace').rstrip()
        if not s:
            continue
        
        # Skip non-ESP-IDF log lines
        if not ('I (' in s or 'W (' in s or 'E (' in s):
            continue
        
        # Color by severity
        if 'E (' in s:
            color = '${COLOR_ERR}'
        elif 'W (' in s:
            color = '${COLOR_WARN}'
        else:
            color = '${COLOR_ESP}'
        
        # Extract timestamp
        t = int((time.time()-t0)*1000)
        # Trim long lines
        if len(s) > 130:
            s = s[:130] + '...'
        
        print(f'{color}[ESP {t:6d}] {s}${COLOR_RESET}')
        
    except serial.SerialException:
        time.sleep(1)
    except Exception as e:
        print(f'${COLOR_ERR}[ESP ERR] {e}${COLOR_RESET}')
        time.sleep(1)

ser.close()
" &

PID_ESP=$!

# Bridge log tailer
python3 -c "
import time, os, re

t0 = time.time()
end = t0 + ${DURATION}
pos = 0

KEY_WORDS = ('WS conectado','hello recibido','Enviando saludo','TTS','STT','VAD:',
             'pipeline','LiteLLM','speaker','gTTS','Edge-TTS','pyttsx','Error',
             'Sesión cerrada','habla detectada','silencio','menu','Escuchando',
             'Listen','Open','frame','send','sesion','session')

while time.time() < end:
    try:
        log = '${BRIDGE_LOG}'
        if os.path.exists(log):
            with open(log, 'r') as f:
                f.seek(pos)
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    # Only show relevant lines
                    show = False
                    for kw in KEY_WORDS:
                        if kw.lower() in line.lower():
                            show = True
                            break
                    if not show:
                        continue
                    
                    t = int((time.time()-t0)*1000)
                    if 'ERROR' in line or 'Error' in line:
                        color = '${COLOR_ERR}'
                    elif 'WARNING' in line or 'warning' in line:
                        color = '${COLOR_WARN}'
                    else:
                        color = '${COLOR_BRIDGE}'
                    
                    if len(line) > 130:
                        line = line[:130] + '...'
                    
                    print(f'{color}[BRIDGE {t:6d}] {line}${COLOR_RESET}')
                pos = f.tell()
    except Exception:
        pass
    time.sleep(0.3)
" &

PID_BRIDGE=$!

wait
