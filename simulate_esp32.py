import asyncio
import websockets
import json
import time

async def simulate():
    uri = "ws://localhost:16000/"
    print(f"🔗 Conectando al bridge en {uri}...")
    
    try:
        async with websockets.connect(uri) as ws:
            # 1. Enviar HELLO (Protocolo xiaozhi)
            hello = {
                "type": "hello",
                "version": 3,
                "device_id": "SIMULATED_ESP32_ORDEN_160"
            }
            await ws.send(json.dumps(hello))
            print("📤 HELLO enviado.")
            
            resp = await ws.recv()
            print(f"📥 Respuesta: {resp}")
            
            # 2. Iniciar escucha (LISTEN)
            listen = {
                "type": "listen",
                "state": "start",
                "mode": "auto"
            }
            await ws.send(json.dumps(listen))
            print("📤 LISTEN start enviado.")
            
            # 3. Esperar saludo del bridge
            while True:
                msg = await ws.recv()
                if isinstance(msg, str):
                    data = json.loads(msg)
                    print(f"📥 JSON recibido: {data}")
                    if data.get("type") == "tts" and data.get("state") == "stop":
                        print("✅ Saludo inicial completado.")
                        break
                else:
                    # Audio bytes (BinaryProtocol3 header)
                    print(f"📥 Audio recibido: {len(msg)} bytes")

            print("\n🚀 SIMULACIÓN EXITOSA: El bridge responde correctamente.")
            
    except Exception as e:
        print(f"❌ Error en la simulación: {e}")

if __name__ == "__main__":
    asyncio.run(simulate())
