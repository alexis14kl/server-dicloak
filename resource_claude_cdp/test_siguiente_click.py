"""Test: verificar que el click en Siguiente funciona."""
import json, time, sys, os, io, base64, ctypes
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import websockets.sync.client as ws_sync
import urllib.request

port = int(sys.argv[1]) if len(sys.argv) > 1 else 57382

targets = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=3).read())
page = next((t for t in targets if t.get("type") == "page"), None)
print(f"Pagina: {page['title'][:60]}")
print(f"URL: {page['url'][:80]}")

ws = ws_sync.connect(page["webSocketDebuggerUrl"], max_size=2**24)
msg_id = 0

def send_cdp(method, params=None):
    global msg_id
    msg_id += 1
    ws.send(json.dumps({"id": msg_id, "method": method, "params": params or {}}))
    return json.loads(ws.recv(timeout=15))

def evaluate(expr):
    r = send_cdp("Runtime.evaluate", {"expression": expr, "returnByValue": True})
    return r.get("result", {}).get("result", {}).get("value")

def screenshot(name):
    r = send_cdp("Page.captureScreenshot", {"format": "png"})
    data = r.get("result", {}).get("data", "")
    if data:
        out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "screenshots")
        os.makedirs(out, exist_ok=True)
        with open(os.path.join(out, name), "wb") as f:
            f.write(base64.b64decode(data))
        print(f"  Screenshot: {name}")

def os_click(x, y):
    ctypes.windll.user32.SetCursorPos(int(x), int(y))
    time.sleep(0.1)
    ctypes.windll.user32.mouse_event(0x02, 0, 0, 0, 0)
    time.sleep(0.05)
    ctypes.windll.user32.mouse_event(0x04, 0, 0, 0, 0)

# 1. Ver estado actual
pwd_len = evaluate("document.querySelector('input[name=\"Passwd\"]')?.value.length || 0")
print(f"\nPassword length: {pwd_len}")

# 2. Screenshot actual
screenshot("siguiente_01_current.png")

# 3. Obtener coords del boton Siguiente
btn_info = evaluate("""(() => {
    const btns = Array.from(document.querySelectorAll('button'));
    return btns.map(b => {
        const rect = b.getBoundingClientRect();
        return {
            text: (b.innerText || '').trim().substring(0, 30),
            x: Math.round(rect.x), y: Math.round(rect.y),
            w: Math.round(rect.width), h: Math.round(rect.height),
            cx: Math.round(rect.x + rect.width/2),
            cy: Math.round(rect.y + rect.height/2),
            visible: rect.width > 0,
        };
    });
})()""")
print(f"\nBotones: {btn_info}")

win = json.loads(evaluate("""JSON.stringify({
    screenX: window.screenX, screenY: window.screenY,
    chromeH: window.outerHeight - window.innerHeight,
})"""))
print(f"Window: {win}")

# Buscar Siguiente
btns = json.loads(btn_info) if isinstance(btn_info, str) else btn_info
sig = None
for b in btns:
    if 'siguiente' in b.get('text', '').lower() or 'next' in b.get('text', '').lower():
        sig = b
        break

if sig:
    sx = win["screenX"] + sig["cx"]
    sy = win["screenY"] + win["chromeH"] + sig["cy"]
    print(f"\nBoton Siguiente: text='{sig['text']}' screen=({sx}, {sy})")

    # 4. OS click en Siguiente
    print("\n=== Click OS en Siguiente ===")
    send_cdp("Page.bringToFront")
    time.sleep(0.5)
    os_click(sx, sy)

    time.sleep(8)

    url = evaluate("window.location.href") or ""
    title = evaluate("document.title") or ""
    print(f"URL despues: {url[:100]}")
    print(f"Titulo: {title}")
    screenshot("siguiente_02_after.png")
else:
    print("No se encontro boton Siguiente!")

ws.close()
