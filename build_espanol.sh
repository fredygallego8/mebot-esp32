#!/bin/bash
set -e

cd "$(dirname "$0")"

echo "=== Configurando firmware MeBot en español para M5Stack CoreS3 ==="

# Cargar entorno ESP-IDF
source /home/fredy/esp/esp-idf/export.sh
export IDF_PYTHON_ENV_PATH=/home/fredy/.espressif/python_env/idf5.5_py3.12_env

# Configurar target ESP32-S3 (genera un sdkconfig limpio desde defaults)
idf.py set-target esp32s3

# ── Parchear sdkconfig con los valores que defaults.local no aplica ──────────
# sdkconfig en la raíz siempre gana sobre sdkconfig.defaults.local en el build.
# Por eso lo editamos directamente después de set-target.
SDKCONFIG="sdkconfig"

patch_config() {
    local key="$1"
    local value="$2"
    if grep -q "^${key}=" "$SDKCONFIG" 2>/dev/null; then
        sed -i "s|^${key}=.*|${key}=${value}|" "$SDKCONFIG"
    elif grep -q "^# ${key} is not set" "$SDKCONFIG" 2>/dev/null; then
        sed -i "s|^# ${key} is not set|${key}=${value}|" "$SDKCONFIG"
    else
        echo "${key}=${value}" >> "$SDKCONFIG"
    fi
}

unset_config() {
    local key="$1"
    if grep -q "^${key}=y" "$SDKCONFIG" 2>/dev/null; then
        sed -i "s|^${key}=y|# ${key} is not set|" "$SDKCONFIG"
    fi
}

echo "Aplicando configuración MeBot..."

# Idioma español
unset_config   CONFIG_LANGUAGE_ZH_CN
patch_config   CONFIG_LANGUAGE_ES_ES        y

# Board M5Stack CoreS3
patch_config   CONFIG_BOARD_TYPE_M5STACK_CORE_S3 y

# PSRAM Quad (CoreS3 usa PSRAM64H en modo Quad por el PCB routing)
unset_config   CONFIG_SPIRAM_MODE_OCT
patch_config   CONFIG_SPIRAM_MODE_QUAD       y

# Main task stack size: 3584 es insuficiente para TLS wss:// → stack overflow
patch_config   CONFIG_ESP_MAIN_TASK_STACK_SIZE 16384

# LV_USE_IMGFONT (necesario para 78__xiaozhi-fonts)
unset_config   CONFIG_LV_USE_IMGFONT
patch_config   CONFIG_LV_USE_IMGFONT          y

# LV_FONT_FMT_TXT_LARGE (fuentes bitmap grandes como puhui_30_4)
unset_config   CONFIG_LV_FONT_FMT_TXT_LARGE
patch_config   CONFIG_LV_FONT_FMT_TXT_LARGE   y

# LV_USE_LODEPNG (necesario para LVGL PNG decoder)
unset_config   CONFIG_LV_USE_LODEPNG
patch_config   CONFIG_LV_USE_LODEPNG          y

# CXX_RTTI (necesario para dynamic_cast en audio_service.cc)
unset_config   CONFIG_COMPILER_CXX_RTTI
patch_config   CONFIG_COMPILER_CXX_RTTI       y

# CXX_EXCEPTIONS (necesario para componentes managed)
unset_config   CONFIG_COMPILER_CXX_EXCEPTIONS
patch_config   CONFIG_COMPILER_CXX_EXCEPTIONS y

# Wake word — deshabilitar custom (requiere modelos multinet no disponibles)
unset_config   CONFIG_USE_CUSTOM_WAKE_WORD
# Activar wake word en español "Hola ESP" (WN9S) y desactivar chino
unset_config   CONFIG_SR_WN_WN9_NIHAOXIAOZHI_TTS
patch_config   CONFIG_SR_WN_WN9S_HIESP          y

# OTA URL del bridge
patch_config   CONFIG_OTA_URL '"https://bot2market.mebot.online/esp32/ota/"'

# Tabla de particiones (Two OTA para tener espacio suficiente)
unset_config   CONFIG_PARTITION_TABLE_SINGLE_APP
unset_config   CONFIG_PARTITION_TABLE_SINGLE_APP_LARGE
unset_config   CONFIG_PARTITION_TABLE_TWO_OTA
unset_config   CONFIG_PARTITION_TABLE_TWO_OTA_LARGE
patch_config   CONFIG_PARTITION_TABLE_CUSTOM      y
patch_config   CONFIG_PARTITION_TABLE_CUSTOM_FILENAME '"partitions.csv"'

# Offset de tabla aumentado para bootloader con Secure Boot (~40K)
patch_config   CONFIG_PARTITION_TABLE_OFFSET      0x10000

# ── Flash Encryption ──────────────────────────────────────────────────────────
patch_config   CONFIG_SECURE_FLASH_ENC_ENABLED              y
patch_config   CONFIG_SECURE_FLASH_ENCRYPTION_MODE_DEVELOPMENT y

# ── Secure Boot v2 ────────────────────────────────────────────────────────────
patch_config   CONFIG_SECURE_BOOT                           y
patch_config   CONFIG_SECURE_BOOT_V2_RSA                    y
patch_config   CONFIG_SECURE_BOOT_BUILD_SIGNED_BINARIES     y

echo "Configuración aplicada. Compilando..."

# Forzar reconfigure para que Kconfig procese los cambios de board
idf.py reconfigure

# ── Inyectar HTML brandeados MeBot IoT ──────────────────────────────────
# managed_components/ es regenerado por reconfigure, sobreescribiendo
# nuestros HTML personalizados. Los copiamos de assets/wifi_custom/ que
# está versionado fuera del alcance del component manager.
echo "Inyectando HTML brandeados MeBot IoT en portal cautivo..."
WIFI_CUSTOM="assets/wifi_custom"
WIFI_TARGET="managed_components/78__esp-wifi-connect/assets"
if [ -f "$WIFI_CUSTOM/wifi_configuration.html" ]; then
    cp "$WIFI_CUSTOM/wifi_configuration.html" "$WIFI_TARGET/wifi_configuration.html"
    cp "$WIFI_CUSTOM/wifi_configuration_done.html" "$WIFI_TARGET/wifi_configuration_done.html"
    echo "  ✓ HTML brandeados inyectados"
else
    echo "  ⚠ assets/wifi_custom no encontrado, usando HTML por defecto"
fi

# Compilar firmware
echo "=== Compilando firmware (puede tardar varios minutos) ==="
idf.py build

# ── Persistir clave de Secure Boot ──────────────────────────────────────────
SB_KEY="build/secure_boot_signing_key.pem"
SB_KEY_SAFE="secure_boot_signing_key.pem"
if [ -f "$SB_KEY" ] && [ ! -f "$SB_KEY_SAFE" ]; then
    cp "$SB_KEY" "$SB_KEY_SAFE"
    echo "  ✓ Clave Secure Boot guardada en $SB_KEY_SAFE"
elif [ -f "$SB_KEY_SAFE" ] && [ ! -f "$SB_KEY" ]; then
    cp "$SB_KEY_SAFE" "$SB_KEY"
    echo "  ✓ Clave Secure Boot restaurada desde $SB_KEY_SAFE"
fi

echo ""
echo "═══ COMPILACIÓN COMPLETADA ═══"
echo "  Flash Encryption:   ACTIVADO (modo DEVELOPMENT)"
echo "  Secure Boot v2 RSA: ACTIVADO"
ls -lh build/mebot.bin 2>/dev/null || ls -lh build/*.bin 2>/dev/null | head -5
echo ""
echo "  Sincronizar a Vercel: ../../../webpages/mebot.online/scripts/sync-firmware.sh"
echo "  Flashear primer dispositivo: ./flash_m5stack.sh"
