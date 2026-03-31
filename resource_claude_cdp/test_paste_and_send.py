"""Test directo: paste prompt + click Create."""
import json, sys, os, io, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from chat_veo3_videos.veo3_session import Veo3Session, _paste_and_send_prompt

port = int(sys.argv[1]) if len(sys.argv) > 1 else 52838
prompt = "a cinematic shot of ocean waves crashing on rocks at sunrise"

s = Veo3Session(port=port)
s.connect()

url = s.evaluate("window.location.href") or ""
print(f"URL: {url[:80]}")

result = _paste_and_send_prompt(s, prompt)
print(f"Resultado: {result}")

time.sleep(2)
body = s.evaluate("document.body?.innerText.substring(0, 300)") or ""
if "Prompt must" in body:
    print("ERROR: Prompt must be provided")
else:
    print("OK - prompt enviado sin error")

s.close()
