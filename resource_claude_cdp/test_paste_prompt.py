"""Test: pegar prompt en Flow de forma que React lo reconozca."""
import json, sys, os, io, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from chat_veo3_videos.veo3_session import Veo3Session

port = int(sys.argv[1]) if len(sys.argv) > 1 else 52280
prompt = "a dog running on the beach at sunset, cinematic"

s = Veo3Session(port=port)
s.connect()

# Limpiar editor
s.evaluate("""(() => {
    const editor = document.querySelector('[contenteditable="true"]');
    if (editor) {
        editor.focus();
        editor.innerText = '';
        editor.dispatchEvent(new Event('input', {bubbles: true}));
    }
})()""")
time.sleep(0.5)

# Metodo 1: execCommand + input event
print("=== Metodo 1: execCommand + input events ===")
safe = json.dumps(prompt)
r = s.evaluate(f"""(() => {{
    const editor = document.querySelector('[contenteditable="true"]');
    if (!editor) return 'NO_EDITOR';
    editor.focus();

    // Limpiar
    editor.innerText = '';

    // Insertar con execCommand
    document.execCommand('insertText', false, {safe});

    // Disparar eventos que React escucha
    editor.dispatchEvent(new Event('input', {{bubbles: true}}));
    editor.dispatchEvent(new Event('change', {{bubbles: true}}));
    editor.dispatchEvent(new InputEvent('input', {{bubbles: true, data: {safe}, inputType: 'insertText'}}));

    return 'OK:' + editor.innerText.length;
}})()""")
print(f"  Resultado: {r}")
time.sleep(1)

# Verificar si Create esta habilitado
btn_state = s.evaluate("""(() => {
    const btns = Array.from(document.querySelectorAll('button'));
    const createBtn = btns.find(b => (b.innerText||'').includes('arrow_forward'));
    if (!createBtn) return 'NO_BTN';
    return 'disabled=' + createBtn.disabled + ' text=' + createBtn.innerText.trim().substring(0,20);
})()""")
print(f"  Boton Create: {btn_state}")

# Click Create
print("\n  Click en Create...")
s.evaluate("""(() => {
    const btns = Array.from(document.querySelectorAll('button'));
    const createBtn = btns.find(b => (b.innerText||'').includes('arrow_forward'));
    if (createBtn) createBtn.click();
})()""")
time.sleep(2)

# Ver si genero error
body = s.evaluate("document.body?.innerText.substring(0, 200)") or ""
if "Prompt must" in body:
    print("  FALLO: 'Prompt must be provided'")
else:
    print("  OK - no hay error visible")

# Metodo 2: Clipboard paste via CDP Input.insertText
print("\n=== Metodo 2: CDP Input.insertText ===")
# Limpiar
s.evaluate("""(() => {
    const editor = document.querySelector('[contenteditable="true"]');
    editor.focus();
    document.execCommand('selectAll');
    document.execCommand('delete');
})()""")
time.sleep(0.5)

# Usar _send_raw para Input.insertText
s._send_raw("Input.insertText", {"text": prompt})
time.sleep(1)

content = s.evaluate("document.querySelector('[contenteditable=\"true\"]')?.innerText || ''")
print(f"  Contenido: {content[:60]}")

btn_state2 = s.evaluate("""(() => {
    const btns = Array.from(document.querySelectorAll('button'));
    const createBtn = btns.find(b => (b.innerText||'').includes('arrow_forward'));
    return createBtn ? 'disabled=' + createBtn.disabled : 'NO_BTN';
})()""")
print(f"  Boton Create: {btn_state2}")

print("\n  Click en Create...")
s.evaluate("""(() => {
    const btns = Array.from(document.querySelectorAll('button'));
    const createBtn = btns.find(b => (b.innerText||'').includes('arrow_forward'));
    if (createBtn) createBtn.click();
})()""")
time.sleep(2)

body2 = s.evaluate("document.body?.innerText.substring(0, 200)") or ""
if "Prompt must" in body2:
    print("  FALLO: 'Prompt must be provided'")
else:
    print("  OK!")

s.close()
