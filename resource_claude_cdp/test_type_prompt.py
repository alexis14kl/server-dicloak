"""Test: teclear prompt carácter por carácter via CDP."""
import json, sys, os, io, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from chat_veo3_videos.veo3_session import Veo3Session

port = int(sys.argv[1]) if len(sys.argv) > 1 else 52280
prompt = "a dog on the beach"

s = Veo3Session(port=port)
s.connect()

# Focus y limpiar editor
s.evaluate("""(() => {
    const editor = document.querySelector('[contenteditable="true"]');
    editor.focus();
    editor.innerText = '';
    editor.dispatchEvent(new Event('input', {bubbles: true}));
})()""")
time.sleep(0.5)

# Focus de nuevo
s.evaluate("document.querySelector('[contenteditable=\"true\"]').focus()")
time.sleep(0.3)

# Teclear cada carácter via CDP Input.dispatchKeyEvent
print(f"Tecleando: {prompt}")
for char in prompt:
    s._send_raw("Input.dispatchKeyEvent", {
        "type": "keyDown",
        "text": char,
    })
    s._send_raw("Input.dispatchKeyEvent", {
        "type": "keyUp",
        "text": char,
    })
    time.sleep(0.02)

time.sleep(1)

content = s.evaluate("document.querySelector('[contenteditable=\"true\"]')?.innerText || ''")
print(f"Contenido: '{content}'")

# Click Create
print("Click en Create...")
s.evaluate("""(() => {
    const btns = Array.from(document.querySelectorAll('button'));
    const createBtn = btns.find(b => (b.innerText||'').includes('arrow_forward'));
    if (createBtn) createBtn.click();
})()""")
time.sleep(2)

body = s.evaluate("document.body?.innerText.substring(0, 300)") or ""
if "Prompt must" in body:
    print("FALLO: Prompt must be provided")
else:
    print("OK!")

s.close()
