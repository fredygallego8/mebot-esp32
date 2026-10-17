#!/bin/bash
# Continuar flasheo M5Stack CoreS3 después de borrado

ESPTOOL="/home/user/.espressif/python_env/idf5.3_py3.12_env/bin/esptool.py"
PORT="/dev/ttyACM0"
BINARY="firmware_m5stack/merged-binary.bin"

echo "=== Continuando flasheo M5Stack CoreS3 ==="
echo
echo "1. Asegúrate de que el M5Stack CoreS3 esté en modo de descarga:"
echo "   - Mantén presionado el botón RESET (al lado del puerto USB) por ~3 segundos"
echo "   - Suelta cuando el LED interno brille en verde"
echo "   - Luego presiona cualquier tecla para continuar..."
read -n 1 -s

echo "2. Flasheando firmware (16MB flash)..."
"$ESPTOOL" --chip esp32s3 --port "$PORT" --baud 460800 \
  --before default_reset --after hard_reset write_flash \
  --flash_mode dio --flash_freq 80m --flash_size 16MB \
  0x0 "$BINARY"

echo "3. Reiniciando dispositivo..."
"$ESPTOOL" --port "$PORT" --chip esp32s3 run

echo
echo "=== ¡Flasheo completado! ==="
echo "El dispositivo se reiniciará y creará un hotspot WiFi llamado 'MeBot-XXXX'"
echo "Conéctate a ese hotspot con tu celular/computadora y ve a http://192.168.4.1"
echo "Introduce los datos de tu WiFi doméstico para configurar el dispositivo."