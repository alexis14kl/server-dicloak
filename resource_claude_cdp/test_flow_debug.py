"""Test debug: paso a paso del login flow."""
import sys, os, io, json, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from chat_veo3_videos.veo3_session import Veo3Session, VEO3_URL

port = int(sys.argv[1]) if len(sys.argv) > 1 else 57382

s = Veo3Session(port=port)
print(f"1. Conectando a puerto {port}...")
ok = s.connect()
print(f"   Conectado: {ok}")

print(f"\n2. URL actual:")
url = s.evaluate("window.location.href") or ""
print(f"   {url[:100]}")

print(f"\n3. is_on_flow: {s.is_on_flow()}")
print(f"   detect_google_login: {s.detect_google_login()}")

print(f"\n4. Ejecutando handle_google_login...")
result = s.handle_google_login(timeout_sec=60)
print(f"   Resultado: {result}")

print(f"\n5. URL final:")
url = s.evaluate("window.location.href") or ""
title = s.evaluate("document.title") or ""
print(f"   URL: {url[:100]}")
print(f"   Titulo: {title}")

s.close()
