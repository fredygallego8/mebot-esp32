#!/bin/bash
# Script para flashear M5Stack CoreS3 — Martillo (Ferretería El Hogar)

set -e

# Detectar puerto automáticamente o usar el primero disponible
PORT="${1:-}"
if [ -z "$PORT" ]; then
    for p in /dev/ttyACM0 /dev/ttyACM1 /dev/ttyUSB0 /dev/ttyUSB1; do
        if [ -e "$p" ]; then PORT="$p"; break; fi
    done
fi
if [ -z "$PORT" ]; then
    echo "ERROR: No se encontró ningún puerto serial. Conecta el ESP32."
    exit 1
fi

BUILD_DIR="build"

echo "=== Flasheando M5Stack CoreS3 ==="
echo "Puerto: $PORT"
echo

# Verificar que el firmware existe
for f in "$BUILD_DIR/mebot.bin" "$BUILD_DIR/bootloader/bootloader.bin" \
          "$BUILD_DIR/partition_table/partition-table.bin"; do
    if [ ! -f "$f" ]; then
        echo "ERROR: No se encontró $f — corre ./build_espanol.sh primero"
        exit 1
    fi
done

echo "━━━ MODO DOWNLOAD (BOOT MODE) ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  CoreS3: mantén presionado BtnA (botón izquierdo del frente)"
echo "          pulsa RST (botón lateral), suelta RST, suelta BtnA"
echo "          El LED se pondrá verde → listo"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Presiona cualquier tecla cuando el LED esté verde..."
read -n 1 -s

echo ""
echo "Flasheando firmware (esto puede tardar ~30 segundos)..."
source ~/esp/esp-idf/export.sh 2>/dev/null || true

python -m esptool --chip esp32s3 --port "$PORT" --baud 460800 \
  --before default_reset --after hard_reset write_flash \
  --flash_mode dio --flash_freq 80m --flash_size 16MB \
  0x0              "$BUILD_DIR/bootloader/bootloader.bin" \
  0x8000           "$BUILD_DIR/partition_table/partition-table.bin" \
  0xd000           "$BUILD_DIR/ota_data_initial.bin" \
  0x20000          "$BUILD_DIR/mebot.bin" \
  0x800000         "$BUILD_DIR/generated_assets.bin"

echo
echo "=== ¡Flasheo completado! ==="
echo "El dispositivo se reiniciará y creará un hotspot WiFi llamado 'Martillo-XXXX'"
echo "Conéctate a ese hotspot con tu celular/computadora y ve a http://192.168.4.1"
echo "Introduce los datos de tu WiFi doméstico para configurar el dispositivo."