#!/bin/bash
# start.sh — Arranca el bridge + iptables redirect 16001→16000
cd "$(dirname "$0")"

# Port redirect: el firmware tiene CONFIG_OTA_URL en puerto 16001
sudo iptables -t nat -C PREROUTING -p tcp --dport 16001 -j REDIRECT --to-port 16000 2>/dev/null || \
sudo iptables -t nat -A PREROUTING -p tcp --dport 16001 -j REDIRECT --to-port 16000
sudo iptables -t nat -C OUTPUT -p tcp --dport 16001 -j REDIRECT --to-port 16000 2>/dev/null || \
sudo iptables -t nat -A OUTPUT -p tcp --dport 16001 -j REDIRECT --to-port 16000

# Env vars loaded directly inside Python main.py using python-dotenv

# Kill existing bridge on port 16000
kill $(lsof -t -i:16000 2>/dev/null) 2>/dev/null
sleep 1

# Start bridge
nohup python3 -m uvicorn main:app --host 0.0.0.0 --port 16000 > /tmp/mebot_bridge.log 2>&1 &
echo "Bridge PID: $!"
sleep 3

# Verify
curl -s http://localhost:16000/stats | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'Bridge OK — uptime={d[\"uptime_s\"]}s sessions={d[\"sessions_total\"]}')"
