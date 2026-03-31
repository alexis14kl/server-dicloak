"""
Test: Screenshot antes y después de click real en password field.
También intenta Input.insertText y Autofill CDP.
"""
import json, time, sys, os, io, base64
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import websockets.sync.client as ws_sync
import urllib.request

port = int(sys.argv[1]) if len(sys.argv) > 1 else 55377

# 1. Conectar
targets = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=3).read())
page = next((t for t in targets if t.get("type") == "page"), None)
ws_url = page["webSocketDebuggerUrl"]
print(f"Pagina: {page['title'][:60]}")

ws = ws_sync.connect(ws_url, max_size=2**24)
msg_id = 0

def send_cdp(method, params=None):
    global msg_id
    msg_id += 1
    msg = {"id": msg_id, "method": method, "params": params or {}}
    ws.send(json.dumps(msg))
    resp = json.loads(ws.recv(timeout=15))
    return resp

def evaluate(expr):
    r = send_cdp("Runtime.evaluate", {"expression": expr, "returnByValue": True})
    result = r.get("result", {}).get("result", {})
    return result.get("value")

def screenshot(filename):
    r = send_cdp("Page.captureScreenshot", {"format": "png"})
    data = r.get("result", {}).get("data", "")
    if data:
        out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "screenshots")
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, filename)
        with open(path, "wb") as f:
            f.write(base64.b64decode(data))
        print(f"Screenshot: {path}")
    else:
        print(f"Screenshot failed: {r}")

# 2. Screenshot ANTES
print("\n=== Screenshot ANTES del click ===")
screenshot("01_before_click.png")

# 3. Obtener coordenadas del campo password
coords_json = evaluate("""(() => {
    const pwd = document.querySelector('input[name="Passwd"]');
    if (!pwd) return null;
    const rect = pwd.getBoundingClientRect();
    return JSON.stringify({
        x: rect.x, y: rect.y, w: rect.width, h: rect.height,
        cx: rect.x + rect.width/2, cy: rect.y + rect.height/2,
    });
})()""")
print(f"Coords: {coords_json}")

if not coords_json:
    print("No password field!")
    ws.close()
    sys.exit(1)

coords = json.loads(coords_json)
cx, cy = coords["cx"], coords["cy"]

# 4. Click REAL
print(f"\n=== Click real en ({cx:.0f}, {cy:.0f}) ===")
send_cdp("Input.dispatchMouseEvent", {"type": "mousePressed", "x": cx, "y": cy, "button": "left", "clickCount": 1})
time.sleep(0.05)
send_cdp("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": cx, "y": cy, "button": "left", "clickCount": 1})

time.sleep(1.5)
screenshot("02_after_click.png")

# 5. Probar Autofill.trigger (puede no existir en esta version)
print("\n=== Intentando Autofill.trigger ===")
try:
    r = send_cdp("Autofill.trigger", {"fieldId": 0})
    print(f"Autofill.trigger: {r}")
except Exception as e:
    print(f"Autofill.trigger error: {e}")

# 6. Probar Autofill.setAddresses (puede activar autofill)
print("\n=== Intentando Autofill.enable ===")
try:
    r = send_cdp("Autofill.enable")
    print(f"Autofill.enable: {r}")
except Exception as e:
    print(f"Autofill.enable error: {e}")

time.sleep(1)
screenshot("03_after_autofill.png")

# 7. Focus + tecla flecha abajo (a veces abre el dropdown de autofill)
print("\n=== Focus + ArrowDown (trigger autofill dropdown) ===")
evaluate("document.querySelector('input[name=\"Passwd\"]').focus()")
time.sleep(0.3)

# Simular tecla ArrowDown
send_cdp("Input.dispatchKeyEvent", {
    "type": "keyDown",
    "key": "ArrowDown",
    "code": "ArrowDown",
    "windowsVirtualKeyCode": 40,
    "nativeVirtualKeyCode": 40,
})
send_cdp("Input.dispatchKeyEvent", {
    "type": "keyUp",
    "key": "ArrowDown",
    "code": "ArrowDown",
    "windowsVirtualKeyCode": 40,
    "nativeVirtualKeyCode": 40,
})

time.sleep(1.5)
screenshot("04_after_arrowdown.png")

# Verificar si se lleno
val = evaluate("document.querySelector('input[name=\"Passwd\"]')?.value.length || 0")
print(f"\nPassword length: {val}")

# 8. Enter para seleccionar autofill si aparecio
if val == 0:
    print("\n=== Intentando Enter para confirmar autofill ===")
    send_cdp("Input.dispatchKeyEvent", {
        "type": "keyDown",
        "key": "Enter",
        "code": "Enter",
        "windowsVirtualKeyCode": 13,
    })
    send_cdp("Input.dispatchKeyEvent", {
        "type": "keyUp",
        "key": "Enter",
        "code": "Enter",
        "windowsVirtualKeyCode": 13,
    })
    time.sleep(1)
    val2 = evaluate("document.querySelector('input[name=\"Passwd\"]')?.value.length || 0")
    print(f"Password length after Enter: {val2}")

screenshot("05_final.png")

ws.close()
print("\nDone. Revisa los screenshots en resource_claude_cdp/screenshots/")
