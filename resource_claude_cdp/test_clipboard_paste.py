"""Test: clipboard paste (Ctrl+V) para que React reconozca el texto."""
import json, sys, os, io, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from chat_veo3_videos.veo3_session import Veo3Session

port = int(sys.argv[1]) if len(sys.argv) > 1 else 52280
prompt = "a dog running on the beach at sunset"

s = Veo3Session(port=port)
s.connect()

# Limpiar y focus
s.evaluate("""(() => {
    const editor = document.querySelector('[contenteditable="true"]');
    editor.focus();
    document.execCommand('selectAll');
    document.execCommand('delete');
})()""")
time.sleep(0.5)

safe = json.dumps(prompt)

# Metodo: copiar al clipboard y simular paste event
print("=== Clipboard paste via navigator.clipboard + paste event ===")
r = s.evaluate(f"""(async () => {{
    const editor = document.querySelector('[contenteditable="true"]');
    if (!editor) return 'NO_EDITOR';
    editor.focus();

    // Copiar al clipboard
    await navigator.clipboard.writeText({safe});

    // Crear y disparar paste event
    const clipboardData = new DataTransfer();
    clipboardData.setData('text/plain', {safe});
    const pasteEvent = new ClipboardEvent('paste', {{
        bubbles: true,
        cancelable: true,
        clipboardData: clipboardData,
    }});
    editor.dispatchEvent(pasteEvent);

    return 'PASTED:' + editor.innerText.length;
}})()""", timeout=10, await_promise=True)
print(f"  Resultado: {r}")

time.sleep(1)
content = s.evaluate("document.querySelector('[contenteditable=\"true\"]')?.innerText || ''")
print(f"  Contenido: '{content[:60]}'")

# Si no funciono, intentar Ctrl+V via CDP
if not content or len(content) < 5:
    print("\n=== Fallback: Ctrl+V via CDP ===")
    # Primero poner en clipboard
    s.evaluate(f"navigator.clipboard.writeText({safe})", timeout=5, await_promise=True)
    time.sleep(0.5)

    s.evaluate("document.querySelector('[contenteditable=\"true\"]').focus()")
    time.sleep(0.3)

    # Ctrl+V
    s._send_raw("Input.dispatchKeyEvent", {
        "type": "keyDown", "key": "v", "code": "KeyV",
        "windowsVirtualKeyCode": 86, "modifiers": 2,  # 2 = Ctrl
    })
    s._send_raw("Input.dispatchKeyEvent", {
        "type": "keyUp", "key": "v", "code": "KeyV",
        "windowsVirtualKeyCode": 86, "modifiers": 2,
    })
    time.sleep(1)

    content = s.evaluate("document.querySelector('[contenteditable=\"true\"]')?.innerText || ''")
    print(f"  Contenido: '{content[:60]}'")

# Click Create
print("\nClick en Create...")
s.evaluate("""(() => {
    const btns = Array.from(document.querySelectorAll('button'));
    const createBtn = btns.find(b => (b.innerText||'').includes('arrow_forward'));
    if (createBtn) createBtn.click();
})()""")
time.sleep(2)

body = s.evaluate("document.body?.innerText.substring(0, 300)") or ""
if "Prompt must" in body:
    print("FALLO")
else:
    print("OK!")

s.close()
