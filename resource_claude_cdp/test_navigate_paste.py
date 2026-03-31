"""Navegar a URL de proyecto, pegar prompt y enviar."""
import json, sys, os, io, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from chat_veo3_videos.veo3_session import Veo3Session, _paste_and_send_prompt

port = int(sys.argv[1]) if len(sys.argv) > 1 else 52838
project_url = "https://labs.google/fx/tools/flow/project/f8124032-5520-47ac-8d19-3f2281d159af"
prompt = "a cinematic shot of ocean waves crashing on rocks at sunrise"

s = Veo3Session(port=port)
s.connect()

# Navegar al proyecto
print(f"Navegando a {project_url[:60]}...")
s.navigate(project_url)
time.sleep(5)

url = s.evaluate("window.location.href") or ""
title = s.evaluate("document.title") or ""
print(f"URL: {url[:80]}")
print(f"Title: {title}")

# Esperar editor
print("Esperando editor...")
for i in range(15):
    has_editor = s.evaluate("!!document.querySelector('[contenteditable=\"true\"]')")
    if has_editor:
        print(f"  Editor listo ({i+1}s)")
        break
    time.sleep(1)
else:
    print("  Editor no apareció")

time.sleep(1)

# Pegar y enviar
print(f"\nPegando prompt: {prompt[:50]}...")
result = _paste_and_send_prompt(s, prompt)
print(f"Resultado: {result}")

s.close()
