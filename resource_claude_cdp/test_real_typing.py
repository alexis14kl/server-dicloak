"""Test: tecleo real char por char via CDP dispatchKeyEvent con charCode."""
import json, sys, os, io, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from chat_veo3_videos.veo3_session import Veo3Session

port = int(sys.argv[1]) if len(sys.argv) > 1 else 52838
prompt = "a dog on beach"

s = Veo3Session(port=port)
s.connect()

# Focus editor
s.evaluate("""(() => {
    const editor = document.querySelector('[contenteditable="true"]');
    editor.focus();
    // Seleccionar todo y borrar
    window.getSelection().selectAllChildren(editor);
    document.execCommand('delete');
})()""")
time.sleep(0.5)

# Verificar que esta vacio
content = s.evaluate("document.querySelector('[contenteditable=\"true\"]')?.innerText || ''")
print(f"Editor antes: '{content.strip()}'")

# Teclear cada caracter con keyDown(char) + keyUp
print(f"Tecleando: {prompt}")
for char in prompt:
    code = ord(char)
    s._send_raw("Input.dispatchKeyEvent", {
        "type": "char",
        "text": char,
        "unmodifiedText": char,
        "key": char,
    })
    time.sleep(0.03)

time.sleep(1)

content = s.evaluate("document.querySelector('[contenteditable=\"true\"]')?.innerText || ''")
print(f"Editor despues: '{content.strip()}'")

# Click Create
print("\nClick en Create...")
r = s.evaluate("""(() => {
    const btns = Array.from(document.querySelectorAll('button'));
    const createBtn = btns.find(b => (b.innerText||'').includes('arrow_forward'));
    if (createBtn) { createBtn.click(); return 'CLICKED'; }
    return 'NO_BTN';
})()""")
print(f"Resultado click: {r}")

time.sleep(3)
body = s.evaluate("document.body?.innerText.substring(0, 500)") or ""
if "Prompt must" in body:
    print("FALLO: Prompt must be provided")
elif "error" in body.lower()[:100]:
    print(f"ERROR: {body[:100]}")
else:
    print("OK!")

s.close()
