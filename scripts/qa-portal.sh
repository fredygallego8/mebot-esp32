#!/bin/bash
# qa-portal.sh — QA de 6 capas para el portal cautivo WiFi del ESP32
# Valida que los HTML brandeados MeBot IoT estén correctamente embebidos.
# Uso: ./scripts/qa-portal.sh [--verbose]

set -euo pipefail

VERBOSE=false
[[ "${1:-}" == "--verbose" ]] && VERBOSE=true

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BRAND_MARKER="MeBot IoT"
PASS=0
FAIL=0
WARN=0

info()  { echo -e "  ℹ️  $1"; }
ok()    { echo -e "  ✓ $1"; PASS=$((PASS+1)); }
fail()  { echo -e "  ✗ $1"; FAIL=$((FAIL+1)); }
warn()  { echo -e "  ⚠ $1"; WARN=$((WARN+1)); }
passfail() {
    if [[ "$1" == "0" ]]; then ok "$2"; else fail "$2"; fi
}

print_header() {
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  $1"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
}

echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║   QA Portal Cautivo — MeBot IoT ESP32               ║"
echo "║   ${ROOT}/scripts/qa-portal.sh"
echo "╚══════════════════════════════════════════════════════╝"

# ============================================================
# LAYER 1: Source HTML (assets/wifi_custom/)
# ============================================================
print_header "Layer 1: Source HTML (assets/wifi_custom/)"

SRC_DIR="$ROOT/assets/wifi_custom"
if [[ ! -d "$SRC_DIR" ]]; then
    fail "assets/wifi_custom/ no existe"
else
    ok "Directorio assets/wifi_custom/ existe"

    for f in wifi_configuration.html wifi_configuration_done.html; do
        file="$SRC_DIR/$f"
        if [[ ! -f "$file" ]]; then
            fail "$f no encontrado en assets/wifi_custom/"
            continue
        fi
        ok "$f existe ($(wc -c < "$file") bytes)"

        if grep -q "$BRAND_MARKER" "$file"; then
            ok "  $f contiene '$BRAND_MARKER'"
        else
            fail "  $f NO contiene '$BRAND_MARKER' — sin branding!"
        fi
    done
fi

# ============================================================
# LAYER 2: Managed Components (injected after reconfigure)
# ============================================================
print_header "Layer 2: Managed Components (injected HTML)"

MC_DIR="$ROOT/managed_components/78__esp-wifi-connect/assets"
if [[ ! -d "$MC_DIR" ]]; then
    fail "managed_components/78__esp-wifi-connect/assets/ no existe"
else
    ok "Directorio managed_components/assets/ existe"

    for f in wifi_configuration.html wifi_configuration_done.html; do
        file="$MC_DIR/$f"
        if [[ ! -f "$file" ]]; then
            fail "$f no encontrado en managed_components/"
            continue
        fi
        ok "$f existe ($(wc -c < "$file") bytes)"

        if grep -q "$BRAND_MARKER" "$file"; then
            ok "  $f contiene '$BRAND_MARKER'"
        else
            fail "  $f NO contiene '$BRAND_MARKER' — revisar build_espanol.sh"
        fi
    done
fi

# Verify build_espanol.sh has the injection step
BUILD_SCRIPT="$ROOT/build_espanol.sh"
if grep -q "Inyectando HTML brandeados" "$BUILD_SCRIPT" 2>/dev/null; then
    ok "build_espanol.sh tiene paso de inyección HTML"
else
    fail "build_espanol.sh NO tiene paso de inyección — branding no sobrevive reconfigure"
fi

# ============================================================
# LAYER 3: Build artifact (.S.obj — compiled binary)
# ============================================================
print_header "Layer 3: Binary artifact (compiled .S.obj / mebot.bin)"

# Search for branded strings in compiled artifacts
BUILD_DIR="$ROOT/build"
FOUND_BRAND=false

# Method 1: Search in .S.obj files (EMBED_TXTFILES compilation artifacts)
S_OBJS=$(find "$BUILD_DIR" -name "*.S.obj" 2>/dev/null || true)
for obj in $S_OBJS; do
    if strings "$obj" 2>/dev/null | grep -q "$BRAND_MARKER"; then
        ok "Branding encontrado en $obj"
        FOUND_BRAND=true
        break
    fi
done

# Method 2: Search in mebot.bin
if [[ "$FOUND_BRAND" == false ]] && [[ -f "$BUILD_DIR/mebot.bin" ]]; then
    if strings "$BUILD_DIR/mebot.bin" 2>/dev/null | grep -q "$BRAND_MARKER"; then
        ok "Branding encontrado en mebot.bin"
        FOUND_BRAND=true
    fi
fi

if [[ "$FOUND_BRAND" == false ]]; then
    fail "Branding NO encontrado en ningún artifact compilado — rebuild necesario"
fi

# ============================================================
# LAYER 4: Bridge HTTP API (localhost:16000)
# ============================================================
print_header "Layer 4: Bridge HTTP API (localhost:16000)"

BRIDGE_URL="http://localhost:16000"

# Check bridge health first
if curl -sf --connect-timeout 3 --max-time 5 "$BRIDGE_URL/health" > /dev/null 2>&1; then
    ok "Bridge reachable en $BRIDGE_URL/health"

    # Check if bridge serves the HTML pages
    for page in wifi_configuration wifi_configuration_done; do
        resp=$(curl -s -o /dev/null -w "%{http_code}" "$BRIDGE_URL/assets/$page.html" 2>/dev/null || echo "000")
        if [[ "$resp" == "200" ]]; then
            ok "Bridge sirve /assets/$page.html (HTTP $resp)"
        else
            warn "Bridge NO sirve /assets/$page.html (HTTP $resp) — no crítico si solo es portal AP"
        fi
    done

    # Check route registration in main.py
    if grep -q "wifi_custom" "$ROOT/bridge/main.py" 2>/dev/null; then
        ok "bridge/main.py tiene rutas para assets/wifi_custom/"
    else
        warn "bridge/main.py NO tiene rutas para assets/wifi_custom/ — bridge no sirve HTML"
    fi
else
    fail "Bridge no responde en $BRIDGE_URL/health — bridge no está corriendo"
fi

# ============================================================
# LAYER 5: generated_assets.bin (assets partition)
# ============================================================
print_header "Layer 5: generated_assets.bin (assets partition)"

ASSETS_BIN="$BUILD_DIR/generated_assets.bin"
if [[ -f "$ASSETS_BIN" ]]; then
    SIZE=$(wc -c < "$ASSETS_BIN")
    ok "generated_assets.bin existe ($SIZE bytes)"

    if strings "$ASSETS_BIN" 2>/dev/null | grep -q "$BRAND_MARKER"; then
        info "generated_assets.bin contiene '$BRAND_MARKER' — HTML también embebido aquí"
    else
        info "generated_assets.bin NO contiene '$BRAND_MARKER' (normal — contiene fonts/emojis, no HTML)"
    fi
else
    info "generated_assets.bin no encontrado — se genera durante build"
fi

# ============================================================
# LAYER 6: End-to-End Checklist
# ============================================================
print_header "Layer 6: End-to-End Checklist"

echo ""
echo "  Para verificar el portal en el ESP32 físico:"
echo ""
echo "  1. Conéctate al hotspot WiFi 'MeBot-XXXX' desde un celular"
echo "     (o desde el navegador del ESP32 en modo AP)"
echo ""
echo "  2. Abre http://192.168.4.1 en MODO INCOGNITO"
echo "     (para evitar caché del navegador)"
echo ""
echo "  3. Verifica visualmente:"
echo "     - Logo MeBot IoT en la parte superior"
echo "     - Tema oscuro (#0F172A)"
echo "     - Texto 'MeBot IoT — Configuración WiFi'"
echo "     - Footer con marca MeBot"
echo ""
echo "  4. Si ves el diseño original (chino):"
echo "     a) Borra caché del navegador o usa modo incógnito"
echo "     b) Revisa que 'generated_assets.bin' no contenga HTML duplicado"
echo "     c) Revisa wifi_configuration_ap.cc en busca de HTML hardcodeado"
echo ""
echo "  5. Verificación con curl (si tienes red local):"
echo "     curl -s http://192.168.4.1 | grep 'MeBot IoT'"
echo ""

# ============================================================
# Summary
# ============================================================
print_header "RESUMEN"

echo ""
echo "  ✅ PASS: $PASS"
echo "  ❌ FAIL: $FAIL"
echo "  ⚠  WARN: $WARN"
echo ""

if [[ "$FAIL" -gt 0 ]]; then
    echo "  ❌ QA FALLÓ — revisa los fallos antes de flashear"
    exit 1
else
    echo "  ✅ QA APROBADO — portal listo para flashear"
    exit 0
fi
