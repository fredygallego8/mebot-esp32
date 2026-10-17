#!/bin/bash
# ── mebot-logs.sh ─────────────────────────────────────────────────────────────
# Muestra en paralelo:
#   - Logs del bridge (AWS, en tiempo real)
#   - Serial del ESP32 (puerto USB local)
#
# Uso:  ./logs.sh
#       ./logs.sh bridge      (solo bridge)
#       ./logs.sh esp32       (solo ESP32)
#       ./logs.sh raw         (bridge sin filtrar, nivel DEBUG completo)
# ─────────────────────────────────────────────────────────────────────────────

KEY=/home/user/Escritorio/pro26/bot_stacks_erp/mebot_stack/NativoBotERP/openclaw-hardened-ansible/mercader.pem
SERVER=openclaw@54.147.195.15
PORT=2222
CONTAINER=mebot-mebot-bridge
SERIAL=/dev/ttyACM0
BAUD=115200

MODE="${1:-both}"

# Colores
RED='\033[0;31m'; GRN='\033[0;32m'; YLW='\033[0;33m'
CYN='\033[0;36m'; BLU='\033[0;34m'; MAG='\033[0;35m'; NC='\033[0m'

bridge_logs() {
    echo -e "${CYN}━━━ BRIDGE (AWS) ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    ssh -i "$KEY" -p "$PORT" -o StrictHostKeyChecking=no "$SERVER" \
        "podman logs -f $CONTAINER 2>&1" | while IFS= read -r line; do
            if   echo "$line" | grep -q "ERROR\|error\|FAILED"; then
                echo -e "${RED}[BRIDGE] $line${NC}"
            elif echo "$line" | grep -q "STT '\|LLM '\|TTS "; then
                echo -e "${GRN}[BRIDGE] $line${NC}"
            elif echo "$line" | grep -q "hello\|Handshake\|OTA\|listen\|Pipeline\|Sesión"; then
                echo -e "${YLW}[BRIDGE] $line${NC}"
            elif echo "$line" | grep -q "DEBUG.*httpcore\|DEBUG.*httpx"; then
                : # filtrar ruido de httpcore en modo normal
            else
                echo -e "${CYN}[BRIDGE] $line${NC}"
            fi
        done
}

bridge_logs_raw() {
    echo -e "${CYN}━━━ BRIDGE RAW (DEBUG completo) ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    ssh -i "$KEY" -p "$PORT" -o StrictHostKeyChecking=no "$SERVER" \
        "podman logs -f $CONTAINER 2>&1" | sed "s/^/${CYN}[BRIDGE] ${NC}/"
}

esp32_logs() {
    echo -e "${MAG}━━━ ESP32 SERIAL ($SERIAL @ ${BAUD}) ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    if [ ! -e "$SERIAL" ]; then
        echo -e "${RED}[ESP32] Puerto $SERIAL no encontrado. Conecta el dispositivo.${NC}"
        return 1
    fi
    python3 - "$SERIAL" "$BAUD" <<'PYEOF'
import sys, serial, time

port = sys.argv[1]
baud = int(sys.argv[2])

RED   = '\033[0;31m'
GRN   = '\033[0;32m'
YLW   = '\033[0;33m'
MAG   = '\033[0;35m'
NC    = '\033[0m'

try:
    s = serial.Serial(port, baud, timeout=1)
    while True:
        try:
            line = s.readline().decode('utf-8', errors='replace').rstrip()
        except Exception:
            time.sleep(0.1)
            continue
        if not line:
            continue

        if any(x in line for x in ['E (', 'ERROR', 'FAILED', 'error']):
            color = RED
        elif any(x in line for x in ['OTA', 'websocket', 'Session ID', 'STT', 'TTS', 'hello']):
            color = YLW
        elif any(x in line for x in ['I (', 'WiFi', 'IP address', 'connected']):
            color = GRN
        else:
            color = MAG

        ts = time.strftime('%H:%M:%S')
        print(f"{color}[ESP32 {ts}] {line}{NC}", flush=True)
except serial.SerialException as e:
    print(f"{RED}[ESP32] Error abriendo {port}: {e}{NC}")
except KeyboardInterrupt:
    pass
PYEOF
}

cleanup() {
    echo -e "\n${YLW}Cerrando logs...${NC}"
    kill 0 2>/dev/null
    exit 0
}
trap cleanup SIGINT SIGTERM

case "$MODE" in
    bridge)
        bridge_logs
        ;;
    raw)
        bridge_logs_raw
        ;;
    esp32)
        esp32_logs
        ;;
    both|*)
        # Lanzar ambos en paralelo con prefijos de color
        bridge_logs  &
        PID_BRIDGE=$!
        esp32_logs   &
        PID_ESP=$!
        echo -e "${YLW}Logs activos — Ctrl+C para salir${NC}"
        wait $PID_BRIDGE $PID_ESP
        ;;
esac
