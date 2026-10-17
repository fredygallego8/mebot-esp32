#!/bin/bash
# ── Tunel SSH → LiteLLM de OpenClaw en AWS ───────────────────────────────────
# Expone el puerto 4000 del servidor en localhost:4000 de esta máquina.
# El bridge (podman) lo alcanza en http://host-gateway:4000/v1
#
# Uso: ./tunnel-litellm.sh
# Ctrl+C para cerrar.

KEY=/home/user/Escritorio/pro26/bot_stacks_erp/mebot_stack/NativoBotERP/openclaw-hardened-ansible/mercader.pem
HOST=54.147.195.15
PORT=2222

echo "Abriendo tunel LiteLLM → localhost:4000 ..."
echo "(Ctrl+C para cerrar)"
echo ""

ssh -N \
  -L 0.0.0.0:4000:localhost:4000 \
  -i "$KEY" \
  -p "$PORT" \
  -o StrictHostKeyChecking=no \
  -o ServerAliveInterval=30 \
  openclaw@"$HOST"
