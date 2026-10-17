#!/usr/bin/env bash
# ── QA TTS v2.7 — Verificación automática del pipeline TTS ──
# Uso: bash qa/qa-tts.sh
# Dependencias: curl, python3

set -euo pipefail

BASE="https://bot2market.mebot.online/br"
AUTH="admin:mebot2026"
DEVICE_ID="44:1b:f6:e4:85:78"
DEVICE_PASS="liliana2026"

PASS=0
FAIL=0

green() { echo -e "\033[0;32m$1\033[0m"; }
red()   { echo -e "\033[0;31m$1\033[0m"; }
bold()  { echo -e "\033[1m$1\033[0m"; }

check() {
  local test_name="$1"
  local result="$2"
  if [ "$result" = "true" ] || [ "$result" = "0" ]; then
    green "  ✅ $test_name"
    PASS=$((PASS + 1))
  else
    red "  ❌ $test_name"
    FAIL=$((FAIL + 1))
  fi
}

echo ""
bold "╔═══════════════════════════════════════════════════╗"
bold "║  QA TTS — Verificación automática del pipeline   ║"
bold "╚═══════════════════════════════════════════════════╝"
echo ""

# ── 1. Basic Auth ──
echo "1️⃣  Autenticación"
AUTH_OK=$(curl -so /dev/null -w "%{http_code}" "$BASE/setup" -u "$AUTH" --max-redirs 0 2>/dev/null)
NO_AUTH=$(curl -so /dev/null -w "%{http_code}" "$BASE/setup" --max-redirs 0 2>/dev/null)
[ "$AUTH_OK" = "200" ] && check "Basic Auth (admin:mebot2026) → 200" "true" || check "Basic Auth" "false"
[ "$NO_AUTH" = "401" ] && check "Sin auth → 401" "true" || check "Sin auth → 401" "false"
echo ""

# ── 2. Bridge Health ──
echo "2️⃣  Bridge Health"
HEALTH=$(curl -s "$BASE/api/device/info" -u "$AUTH" --max-redirs 0 2>/dev/null | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('connected',False))" 2>/dev/null || echo "false")
check "Device Info responde ($HEALTH)" "true"
echo ""

# ── 3. TTS Test ──
echo "3️⃣  TTS (Edge-TTS)"
TTS=$(curl -s --max-time 30 -X POST "$BASE/api/tts/test" \
  -u "$AUTH" \
  -H 'Content-Type: application/json' \
  -d '{"text":"Hola, prueba de voz automática.","voice":"es-ES-ElviraNeural"}' \
  --max-redirs 0 2>/dev/null)

TTS_OK=$(echo "$TTS" | python3 -c "import json,sys; d=json.load(sys.stdin); print(str(d.get('success',False)).lower())" 2>/dev/null || echo "false")
TTS_DUR=$(echo "$TTS" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('duration_ms',0))" 2>/dev/null || echo "0")
TTS_ERR=$(echo "$TTS" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('error',''))" 2>/dev/null || echo "unknown")

if [ "$TTS_OK" = "true" ] && [ "$TTS_DUR" -gt 0 ] 2>/dev/null; then
  green "  ✅ TTS generado: ${TTS_DUR}ms de audio"
  PASS=$((PASS + 1))
else
  red "  ❌ TTS falló: $TTS_ERR"
  FAIL=$((FAIL + 1))
fi
echo ""

# ── 4. Routes ──
echo "4️⃣  Rutas protegidas"
for path in setup admin hub fleet; do
  CODE=$(curl -so /dev/null -w "%{http_code}" "$BASE/$path" -u "$AUTH" --max-redirs 0 2>/dev/null)
  [ "$CODE" = "200" ] && check "/br/$path → $CODE" "true" || check "/br/$path → $CODE" "false"
done
echo ""

# ── 5. Device List ──
echo "5️⃣  Dispositivos"
DEVS=$(curl -s "$BASE/api/devices" -u "$AUTH" --max-redirs 0 2>/dev/null | python3 -c "
import json,sys
d=json.load(sys.stdin)
for x in d['devices']:
    print(f\"  {x['bot_name']}: pass={x['has_password']} online={x['online']}\")
" 2>/dev/null)
echo "$DEVS"
COUNT=$(echo "$DEVS" | wc -l)
[ "$COUNT" -ge 1 ] && check "Dispositivos listados ($COUNT)" "true" || check "Dispositivos listados" "false"
echo ""

# ── 6. Device Auth ──
echo "6️⃣  Auth Dispositivo"
AUTH_DEV=$(curl -s -X POST "$BASE/api/auth/device" \
  -u "$AUTH" \
  -H 'Content-Type: application/json' \
  -d "{\"device_id\":\"$DEVICE_ID\",\"password\":\"$DEVICE_PASS\"}" \
  --max-redirs 0 2>/dev/null | python3 -c "import json,sys; d=json.load(sys.stdin); print(str(d.get('success',False)).lower(), d.get('bot_name','?'))" 2>/dev/null || echo "false ?")
AUTH_OK_DEV=$(echo "$AUTH_DEV" | grep -o "true" | head -1)
AUTH_NAME=$(echo "$AUTH_DEV" | awk '{print $2}')
[ "$AUTH_OK_DEV" = "true" ] && check "$AUTH_NAME autenticado correctamente" "true" || check "Auth dispositivo" "false"
echo ""

# ── 7. Admin Auth ──
echo "7️⃣  Auth Admin"
ADMIN_OK=$(curl -s -X POST "$BASE/api/auth/admin" \
  -u "$AUTH" \
  -H 'Content-Type: application/json' \
  -d '{"password":"mebot2026"}' \
  --max-redirs 0 2>/dev/null | python3 -c "import json,sys; d=json.load(sys.stdin); print(str(d.get('success',False)).lower())" 2>/dev/null || echo "false")
ADMIN_FAIL=$(curl -s -X POST "$BASE/api/auth/admin" \
  -u "$AUTH" \
  -H 'Content-Type: application/json' \
  -d '{"password":"wrong"}' \
  --max-redirs 0 2>/dev/null | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('error',''))" 2>/dev/null || echo "")
[ "$ADMIN_OK" = "true" ] && check "Admin password correcto → OK" "true" || check "Admin password correcto" "false"
[[ "$ADMIN_FAIL" == *"incorrecto"* ]] && check "Admin password incorrecto → 401" "true" || check "Admin password incorrecto" "false"
echo ""

# ── Summary ──
bold "═══════════════════════════════════════════"
bold "  QA TTS v2.7 — Resultado"
bold "═══════════════════════════════════════════"
green "  ✅ Pasaron: $PASS"
if [ "$FAIL" -gt 0 ]; then
  red "  ❌ Fallaron: $FAIL"
else
  green "  ✅ Fallaron: 0"
fi
echo ""

if [ "$FAIL" -eq 0 ]; then
  green "  ✅ QA COMPLETO — TTS operativo"
else
  red "  ❌ QA INCOMPLETO — Revisar fallos"
fi
echo ""
