#!/usr/bin/env python3
"""Servidor Python para recovery_dashboard.html — ejecuta scripts .sh adaptados."""
import http.server, socketserver, urllib.parse, subprocess, sys, os, json, time

PORT = 8888
DIR = "/home/fredy/Escritorio/projx262/bot_stacks_erp/mebot_stack/bot-agent/voiceiot/mebot-esp32"

class Handler(http.server.SimpleHTTPRequestHandler):
    def do_POST(self):
        try:
            length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(length).decode('utf-8')
            data = urllib.parse.parse_qs(body) if body else {}
            action = data.get('action', [''])[0]
            res = {"action": action, "status": "ok", "output": "", "ts": time.strftime("%H:%M:%S")}
            if action == "status_port":
                r = subprocess.run(["ls", "-l", "/dev/ttyACM*"], capture_output=True, text=True)
                res["output"] = r.stdout or r.stderr
            elif action == "read_efuses":
                r = subprocess.run(["bash", "-c", f"source ~/esp/esp-idf/export.sh && export IDF_PYTHON_ENV_PATH=/home/fredy/.espressif/python_env/idf5.5_py3.12_env && espefuse.py -p /dev/ttyACM0 summary"], capture_output=True, text=True, cwd=DIR)
                res["output"] = r.stdout + r.stderr
            elif action == "burn_flash_crypto":
                r = subprocess.run(["bash", "-c", f"source ~/esp/esp-idf/export.sh && export IDF_PYTHON_ENV_PATH=/home/fredy/.espressif/python_env/idf5.5_py3.12_env && idf.py efuse flash_encryption"], capture_output=True, text=True, cwd=DIR)
                res["output"] = r.stdout + r.stderr
            elif action == "burn_secure_boot":
                r = subprocess.run(["bash", "-c", f"source ~/esp/esp-idf/export.sh && export IDF_PYTHON_ENV_PATH=/home/fredy/.espressif/python_env/idf5.5_py3.12_env && idf.py efuse secure_boot_v2_enable"], capture_output=True, text=True, cwd=DIR)
                res["output"] = r.stdout + r.stderr
            elif action == "erase_flash":
                r = subprocess.run(["bash", "-c", f"source ~/esp/esp-idf/export.sh && export IDF_PYTHON_ENV_PATH=/home/fredy/.espressif/python_env/idf5.5_py3.12_env && idf.py -p /dev/ttyACM0 erase_flash"], capture_output=True, text=True, cwd=DIR)
                res["output"] = r.stdout + r.stderr
            elif action == "flash":
                r = subprocess.run(["bash", "-c", f"source ~/esp/esp-idf/export.sh && export IDF_PYTHON_ENV_PATH=/home/fredy/.espressif/python_env/idf5.5_py3.12_env && idf.py -p /dev/ttyACM0 flash"], capture_output=True, text=True, cwd=DIR)
                res["output"] = r.stdout + r.stderr
            else:
                res["status"] = "unknown_action"
                res["output"] = f"Acción '{action}' no reconocida."
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(res).encode())
        except Exception as e:
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status":"error","output":str(e)}).encode())

    def do_GET(self):
        if self.path.startswith("/"):
            p = self.path.lstrip("/") or "recovery_dashboard.html"
            fp = os.path.join(DIR, p)
            if not os.path.isfile(fp):
                fp = os.path.join(DIR, "recovery_dashboard.html")
            try:
                with open(fp, "rb") as f:
                    data = f.read()
                ct = "text/html" if fp.endswith(".html") else ("text/plain" if fp.endswith(".sh") else "application/octet-stream")
                self.send_response(200)
                self.send_header("Content-Type", ct)
                self.end_headers()
                self.wfile.write(data)
            except Exception as e:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(str(e).encode())
        else:
            self.send_response(404)
            self.end_headers()

if __name__ == "__main__":
    with socketserver.TCPServer(("", PORT), Handler) as httpd:
        print(f"Servidor de recovery iniciado en http://localhost:{PORT}/")
        print(f"Dashboard: http://localhost:{PORT}/recovery_dashboard.html")
        print("Presiona Ctrl+C para salir.")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nServidor detenido.")
