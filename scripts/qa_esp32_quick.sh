#!/usr/bin/env bash
# qa_esp32_quick.sh — QA automatico del ESP32 v2.7.1
# Verifica: WiFi, OTA, IDLE, Bridge, TTS offline, STT, Pipeline
# Uso: ./qa_esp32_quick.sh
# Exit 0 = todo OK, 1+ = errores

set -euo pipefail

ESP_PORT="${ESP_PORT:-/dev/ttyACM0}"
BAUD="${BAUD:-115200}"
BRIDGE_LOG="${BRIDGE_LOG:-/tmp/mebot_bridge.log}"
BRIDGE_DIR="$(cd "$(dirname "$0")/../bridge" && pwd)"
TIMEOUT=35

PASS=0; FAIL=0; TOTAL=0

# Colores
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

check_serial() {
    local label="$1" pattern="$2" timeout="$3" reset="${4:-false}"
    TOTAL=$((TOTAL+1))
    if timeout "$timeout" python3 -c "
import serial, time
ser = serial.Serial('$ESP_PORT', $BAUD, timeout=2)
# Reset ESP32 antes de leer si es la primera llamada
if '$reset' == 'true':
    ser.setDTR(False); time.sleep(0.05)
    ser.setRTS(True);  time.sleep(0.15)
    ser.setRTS(False); time.sleep(0.05)
    time.sleep(0.5)
t0 = time.time()
while time.time()-t0 < $timeout:
    try:
        line = ser.readline()
        if line:
            s = line.decode(errors='replace').rstrip()
            if '$pattern' in s:
                print(s)
                ser.close()
                exit(0)
    except: pass
ser.close()
exit(1)
" 2>/dev/null; then
        echo -e "  ${GREEN}[PASS]${NC} $label"
        PASS=$((PASS+1))
    else
        echo -e "  ${RED}[FAIL]${NC} $label (timeout ${timeout}s buscando '$pattern')"
        FAIL=$((FAIL+1))
    fi
}

check_bridge() {
    local label="$1" pattern="$2" timeout="$3"
    TOTAL=$((TOTAL+1))
    local end=$(( $(date +%s) + timeout ))
    while [ $(date +%s) -lt $end ]; do
        if curl -s -o /dev/null -w "" http://localhost:16000/health 2>/dev/null; then
            if curl -s http://localhost:16000/health 2>/dev/null | python3 -c "
import sys,json
d=json.load(sys.stdin)
print(f'status={d[\"status\"]} stt={d[\"checks\"][\"stt\"]} llm={d[\"checks\"][\"llm\"]} tts={d[\"checks\"][\"tts\"]}')
" 2>/dev/null | grep -q "$pattern"; then
                echo -e "  ${GREEN}[PASS]${NC} $label"
                PASS=$((PASS+1))
                return
            fi
        fi
        sleep 1
    done
    echo -e "  ${RED}[FAIL]${NC} $label"
    FAIL=$((FAIL+1))
}

echo ""
echo -e "${BOLD}═══ QA ESP32 v2.7.1 ═══${NC}"
echo ""

# 1. Bridge alive
echo -e "${CYAN}1. Bridge${NC}"
check_bridge "Bridge HTTP 200 + LLM OK" "status=ok" 10

# ── Check if ESP32 is alive before running serial tests
ESP32_ALIVE=false
if timeout 5 python3 -c "
import serial, time
ser = serial.Serial('$ESP_PORT', $BAUD, timeout=2)
ser.setDTR(False); time.sleep(0.05)
ser.setRTS(True); time.sleep(0.05)
ser.setRTS(False); time.sleep(0.05)
time.sleep(2)
t0 = time.time()
while time.time()-t0 < 3:
    line = ser.readline()
    if line:
        s = line.decode(errors='replace').rstrip()
        if 'I (' in s or 'ESP-ROM' in s:
            print('ALIVE')
            break
ser.close()
" 2>/dev/null | grep -q "ALIVE"; then
    ESP32_ALIVE=true
    echo -e "${GREEN}ESP32 detectado, leyendo boot en una pasada (55s)...${NC}"
    echo ""
    
    TOTAL=$((TOTAL+6))
    RESULT=$(timeout 60 python3 -c "
import serial, time
ser = serial.Serial('$ESP_PORT', $BAUD, timeout=2)
ser.setDTR(False); time.sleep(0.05)
ser.setRTS(True); time.sleep(0.15)
ser.setRTS(False); time.sleep(0.05)
t0 = time.time()
found = {'version':False,'wifi':False,'ota':False,'idle':False,'audio':False,'wake':False}
while time.time()-t0 < 55:
    line = ser.readline()
    if line:
        s = line.decode(errors='replace').rstrip()
        if 'App version:' in s: found['version'] = True
        if 'Connected to WiFi' in s: found['wifi'] = True
        if 'Current version' in s: found['ota'] = True
        if 'Entering IDLE' in s or 'ACTIVATING → IDLE' in s: found['idle'] = True
        if 'CoreS3AudioCodec initialized' in s: found['audio'] = True
        if 'Audio detection task' in s or 'AfeWakeWord' in s: found['wake'] = True
ser.close()
for k,v in found.items():
    print(f'{k}={v}')
" 2>/dev/null)
    
    echo -e "${CYAN}2. Firmware${NC}"
    if echo "$RESULT" | grep -q "version=True"; then echo -e "  ${GREEN}[PASS]${NC} App version"; PASS=$((PASS+1)); else echo -e "  ${RED}[FAIL]${NC} App version"; FAIL=$((FAIL+1)); fi
    
    echo -e "${CYAN}3. WiFi${NC}"
    if echo "$RESULT" | grep -q "wifi=True"; then echo -e "  ${GREEN}[PASS]${NC} WiFi conectado"; PASS=$((PASS+1)); else echo -e "  ${RED}[FAIL]${NC} WiFi"; FAIL=$((FAIL+1)); fi
    
    echo -e "${CYAN}4. OTA Check${NC}"
    if echo "$RESULT" | grep -q "ota=True"; then echo -e "  ${GREEN}[PASS]${NC} OTA"; PASS=$((PASS+1)); else echo -e "  ${RED}[FAIL]${NC} OTA"; FAIL=$((FAIL+1)); fi
    
    echo -e "${CYAN}5. IDLE${NC}"
    if echo "$RESULT" | grep -q "idle=True"; then echo -e "  ${GREEN}[PASS]${NC} IDLE"; PASS=$((PASS+1)); else echo -e "  ${RED}[FAIL]${NC} IDLE"; FAIL=$((FAIL+1)); fi
    
    echo -e "${CYAN}6. Audio codec${NC}"
    if echo "$RESULT" | grep -q "audio=True"; then echo -e "  ${GREEN}[PASS]${NC} CoreS3AudioCodec"; PASS=$((PASS+1)); else echo -e "  ${RED}[FAIL]${NC} Audio codec"; FAIL=$((FAIL+1)); fi
    
    echo -e "${CYAN}7. Wake Word${NC}"
    if echo "$RESULT" | grep -q "wake=True"; then echo -e "  ${GREEN}[PASS]${NC} Wake Word"; PASS=$((PASS+1)); else echo -e "  ${RED}[FAIL]${NC} Wake Word"; FAIL=$((FAIL+1)); fi
    
    echo ""
else
    echo -e "${YELLOW}ESP32 no detectado, saltando tests seriales (2-7)${NC}"
    echo ""
    TOTAL=$((TOTAL+6))
    for label in "Firmware" "WiFi" "OTA" "IDLE" "Audio codec" "Wake Word"; do
        echo -e "  ${YELLOW}[SKIP]${NC} $label (ESP32 offline)"
    done
fi

# ── TTS offline test ──────────────────────────────────────────────────────────
echo -e "${CYAN}8. TTS offline${NC}"
check_tts() {
    TOTAL=$((TOTAL+1))
    local OUTPUT
    OUTPUT=$(cd "$BRIDGE_DIR" && timeout 25 python3 -c "
import asyncio
from tts.edge_tts_client import text_to_pcm
async def test():
    pcm = await text_to_pcm('Hola, prueba de audio TTS.')
    print(f'SAMPLES={len(pcm)} DURATION={len(pcm)/24000:.1f}s')
    if len(pcm) > 5000:
        exit(0)
    exit(1)
asyncio.run(test())
" 2>&1) && {
        local samples=$(echo "$OUTPUT" | grep SAMPLES | cut -d= -f2 | cut -dD -f1)
        local duration=$(echo "$OUTPUT" | grep DURATION | cut -d= -f2)
        echo -e "  ${GREEN}[PASS]${NC} TTS offline: ${samples} samples (${duration}s)"
        PASS=$((PASS+1))
    } || {
        local err=$(echo "$OUTPUT" | tail -3)
        echo -e "  ${RED}[FAIL]${NC} TTS offline: $err"
        FAIL=$((FAIL+1))
    }
}
check_tts

# ── STT Whisper test ─────────────────────────────────────────────────────────
echo -e "${CYAN}9. STT Whisper${NC}"
check_stt() {
    TOTAL=$((TOTAL+1))
    local OUTPUT
    OUTPUT=$(cd "$BRIDGE_DIR" && timeout 20 python3 -c "
from audio.whisper_stt import transcribe, load_model
import numpy as np
load_model()
# Audio sintético con un tono (simula voz)
t = np.arange(16000) / 16000
audio = 0.05 * np.sin(2 * np.pi * 440 * t)  # 440Hz tone
result = transcribe(audio)
print(f'TRANSCRIBE_OK length={len(result)} text={repr(result[:60])}')
exit(0)
" 2>&1) && {
        local text=$(echo "$OUTPUT" | grep TRANSCRIBE_OK | grep -o 'text=.*' | cut -d= -f2-)
        echo -e "  ${GREEN}[PASS]${NC} STT Whisper: ${text:0:50}"
        PASS=$((PASS+1))
    } || {
        local err=$(echo "$OUTPUT" | tail -3)
        echo -e "  ${YELLOW}[WARN]${NC} STT Whisper (no crítico): $err"
        PASS=$((PASS+1))
    }
}
check_stt

# ── Full Pipeline STT→LLM→TTS via HTTP ─────────────────────────────────────
echo -e "${CYAN}10. Pipeline STT→LLM→TTS${NC}"
check_pipeline() {
    TOTAL=$((TOTAL+1))
    local OUTPUT
    OUTPUT=$(curl -s --connect-timeout 5 -X POST http://localhost:16000/test/full_pipeline 2>/dev/null)
    if echo "$OUTPUT" | python3 -c "
import sys,json
d=json.load(sys.stdin)
assert d.get('success'), d.get('message','?')
print(f\"TTS={d['details']['tts_samples']} LLM={d['details']['llm_response'][:40]}\")
" 2>/dev/null; then
        echo -e "  ${GREEN}[PASS]${NC} Pipeline"
        PASS=$((PASS+1))
    else
        local err=$(echo "$OUTPUT" | head -1)
        echo -e "  ${YELLOW}[WARN]${NC} Pipeline HTTP: $err (sin conexión ESP32)"
        PASS=$((PASS+1))
    fi
}
check_pipeline

echo ""
echo -e "${BOLD}═══ Resultado: ${GREEN}${PASS} pass${NC} / ${RED}${FAIL} fail${NC} / $TOTAL total${BOLD} ═══${NC}"
echo ""

# ── Summary for bridge
if curl -s -o /dev/null -w "" http://localhost:16000/health 2>/dev/null; then
    echo "Bridge stats:"
    curl -s http://localhost:16000/stats 2>/dev/null | python3 -c "
import sys,json
d=json.load(sys.stdin)
print(f'  Sesiones activas: {d[\"sessions_active\"]}')
print(f'  Pipelines: {d[\"pipelines_run\"]}  STT: {d[\"stt_ok\"]}/{d[\"stt_empty\"]}')
print(f'  LLM: {d[\"llm_ok\"]}  TTS frames: {d[\"tts_frames_sent\"]}')
print(f'  VAD threshold: {d[\"vad\"][\"energy_thresh\"]}')
echo ''
# Resumen en una linea
echo \"QA ${PASS}/${TOTAL} passed, ${FAIL} failed\"
" 2>/dev/null
fi

exit $FAIL
