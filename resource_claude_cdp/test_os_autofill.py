"""
Test: Click OS real en password + click en tooltip DiCloak para autofill.
"""
import json, time, sys, os, io, base64, ctypes
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import websockets.sync.client as ws_sync
import urllib.request

port = int(sys.argv[1]) if len(sys.argv) > 1 else 55377

targets = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=3).read())
page = next((t for t in targets if t.get("type") == "page"), None)
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

def check_password():
    return evaluate("document.querySelector('input[name=\"Passwd\"]')?.value.length || 0")

def os_click(x, y):
    """Click real del OS en coordenadas de pantalla."""
    ctypes.windll.user32.SetCursorPos(int(x), int(y))
    time.sleep(0.1)
    ctypes.windll.user32.mouse_event(0x02, 0, 0, 0, 0)  # left down
    time.sleep(0.05)
    ctypes.windll.user32.mouse_event(0x04, 0, 0, 0, 0)  # left up

# Limpiar campo password
evaluate("""(() => {
    const pwd = document.querySelector('input[name="Passwd"]');
    if (pwd) { pwd.value = ''; pwd.dispatchEvent(new Event('input', {bubbles:true})); }
})()""")

# Traer al frente
send_cdp("Page.bringToFront")
time.sleep(0.5)

# Obtener coordenadas de pantalla
win = json.loads(evaluate("""JSON.stringify({
    screenX: window.screenX, screenY: window.screenY,
    outerH: window.outerHeight, innerH: window.innerHeight,
})"""))

field = json.loads(evaluate("""(() => {
    const pwd = document.querySelector('input[name="Passwd"]');
    const rect = pwd.getBoundingClientRect();
    return JSON.stringify({cx: rect.x + rect.width/2, cy: rect.y + rect.height/2,
        bottom: rect.y + rect.height, x: rect.x, w: rect.width});
})()"""))

chrome_h = win["outerH"] - win["innerH"]
screen_cx = win["screenX"] + field["cx"]
screen_cy = win["screenY"] + chrome_h + field["cy"]
# Tooltip aparece justo debajo del campo
screen_tooltip_y = win["screenY"] + chrome_h + field["bottom"] + 25

print(f"Window: screenX={win['screenX']}, screenY={win['screenY']}, chrome_h={chrome_h}")
print(f"Campo password: centro=({screen_cx:.0f}, {screen_cy:.0f})")
print(f"Tooltip estimado: ({screen_cx:.0f}, {screen_tooltip_y:.0f})")
print(f"Password antes: {check_password()}")

# === PASO 1: Click en campo password ===
print("\n=== PASO 1: Click OS en campo password ===")
os_click(screen_cx, screen_cy)
print("  Click enviado. Esperando tooltip (3s)...")
time.sleep(3)

val = check_password()
print(f"  Password length: {val}")
screenshot("autofill_01_after_field_click.png")

# === PASO 2: Click en el tooltip (debajo del campo) ===
if not val or int(val) == 0:
    print("\n=== PASO 2: Click OS en tooltip (debajo del campo) ===")
    os_click(screen_cx, screen_tooltip_y)
    print("  Click en tooltip enviado. Esperando autofill (3s)...")
    time.sleep(3)

    val = check_password()
    print(f"  Password length: {val}")
    screenshot("autofill_02_after_tooltip_click.png")

# Si aun no funciono, intentar varias posiciones debajo
if not val or int(val) == 0:
    print("\n=== PASO 2b: Intentando varias posiciones del tooltip ===")
    # Primero click en password de nuevo para que reaparezca el tooltip
    os_click(screen_cx, screen_cy)
    time.sleep(2)

    for offset in [15, 30, 45, 60, 80]:
        tooltip_y = win["screenY"] + chrome_h + field["bottom"] + offset
        print(f"  Probando offset={offset} -> y={tooltip_y:.0f}")
        os_click(screen_cx, tooltip_y)
        time.sleep(1.5)

        val = check_password()
        print(f"  Password length: {val}")
        if val and int(val) > 0:
            print(f"  >>> AUTOFILL FUNCIONO con offset={offset}!")
            screenshot(f"autofill_03_success_offset{offset}.png")
            break

# === PASO 3: Si se lleno, click en Siguiente ===
val = check_password()
print(f"\n=== RESULTADO: password length = {val} ===")

if val and int(val) > 0:
    print("\n=== PASO 3: Click en Siguiente ===")
    btn_info = evaluate("""(() => {
        const btns = Array.from(document.querySelectorAll('button'));
        const next = btns.find(b => (b.innerText || '').includes('Siguiente'));
        if (next) {
            const rect = next.getBoundingClientRect();
            return JSON.stringify({cx: rect.x + rect.width/2, cy: rect.y + rect.height/2, text: next.innerText.trim()});
        }
        return null;
    })()""")

    if btn_info:
        btn = json.loads(btn_info)
        btn_screen_x = win["screenX"] + btn["cx"]
        btn_screen_y = win["screenY"] + chrome_h + btn["cy"]
        print(f"  Boton '{btn['text']}' en ({btn_screen_x:.0f}, {btn_screen_y:.0f})")
        os_click(btn_screen_x, btn_screen_y)
        time.sleep(5)

        url = evaluate("window.location.href") or ""
        title = evaluate("document.title") or ""
        print(f"  URL: {url[:100]}")
        print(f"  Titulo: {title}")
        screenshot("autofill_04_after_siguiente.png")

ws.close()
print("\nDone.")
