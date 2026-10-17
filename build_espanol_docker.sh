#!/bin/bash
set -e

echo "=== Compilando firmware en español usando Docker ==="

# Verificar que Docker esté disponible
command -v docker >/dev/null 2>&1 || { echo "Docker no encontrado. Instala Docker."; exit 1; }

# Directorio del proyecto
PROJECT_DIR="$(pwd)"
BUILD_DIR="${PROJECT_DIR}/build_docker"
FIRMWARE_OUT="${BUILD_DIR}/mebot.bin"

# Limpiar builds anteriores
rm -rf "${BUILD_DIR}"
mkdir -p "${BUILD_DIR}"

# Configuración de idioma
CONFIG_FILE="${PROJECT_DIR}/sdkconfig.defaults.local"
if [ ! -f "${CONFIG_FILE}" ]; then
    echo "CONFIG_LANGUAGE_ES_ES=y" > "${CONFIG_FILE}"
    echo "CONFIG_BOARD_TYPE_M5STACK_CORE_S3=y" >> "${CONFIG_FILE}"
fi

echo "Usando imagen ESP-IDF v5.5.2 (compatible con el proyecto)"
echo "Este proceso puede tardar varios minutos..."

# Ejecutar docker con la imagen de ESP-IDF
docker run --rm \
    -v "${PROJECT_DIR}:/project" \
    -v "${BUILD_DIR}:/build" \
    -e "SDKCONFIG_DEFAULTS=/project/sdkconfig.defaults.local" \
    espressif/idf:release-v5.5 \
    /bin/bash -c "
    cd /project && \
    idf.py set-target esp32s3 && \
    idf.py build && \
    cp build/mebot.bin /build/mebot.bin && \
    echo 'Compilación completada'"

if [ -f "${FIRMWARE_OUT}" ]; then
    echo "=== Firmware compilado exitosamente ==="
    echo "Firmware: ${FIRMWARE_OUT}"
    echo "Tamaño: $(ls -lh ${FIRMWARE_OUT} | awk '{print $5}')"
    echo ""
    echo "Para flashear, ejecuta:"
    echo "./flash_m5stack.sh ${FIRMWARE_OUT}"
else
    echo "Error: No se generó el firmware."
    exit 1
fi