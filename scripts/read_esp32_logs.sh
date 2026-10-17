#!/usr/bin/env bash
# read_esp32_logs.sh — Lee logs serial del ESP32 desde USB (idf_monitor + Python fallback)
# Uso: ./read_esp32_logs.sh [--tail] [--baud 115200] [--output file.log]
# Por defecto: 30 segundos de logs, filtro por nivel (ERROR/WARNING/INFO)

set -euo pipefail

PORT="${1:-/dev/ttyACM0}"
BAUD="${BAUD:-115200}"
DURATION="${DURATION:-30}"
OUTPUT="${OUTPUT:-}"

# Colores
RED='\033[0;31m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

if [ ! -c "$PORT" ]; then
    echo "Error: $PORT no existe. Conecta el ESP32 por USB."
    exit 1
fi

if ! groups | grep -q dialout; then
    echo "⚠  No estás en el grupo dialout. Ejecuta: sudo usermod -aG dialout $USER"
fi

echo "📡 Conectando a $PORT a ${BAUD} baud..."
echo "⏱  Leyendo ${DURATION}s de logs. Ctrl+C para interrumpir."
echo ""

# NOTA: Para idf_monitor.py interactivo, corre: 
#   . /home/user/esp/esp-idf/export.sh && idf.py monitor --port /dev/ttyACM0

# Fallback: Python serial directo
python3 -c "
import serial, sys, time, signal
signal.signal(signal.SIGINT, lambda s, f: sys.exit(0))
ser = serial.Serial('${PORT}', ${BAUD}, timeout=1)
t0 = time.time()
dur = ${DURATION}
while time.time() - t0 < dur:
    try:
        line = ser.readline()
        if line:
            s = line.decode(errors='replace').rstrip()
            if 'ERROR' in s:
                s = '\033[0;31m' + s + '\033[0m'
            elif 'WARNING' in s:
                s = '\033[1;33m' + s + '\033[0m'
            elif 'I (' in s:
                s = '\033[0;36m' + s + '\033[0m'
            print(s)
    except: break
ser.close()
" 2>&1

echo ""
echo "✅ Lectura completada (${DURATION}s)"
