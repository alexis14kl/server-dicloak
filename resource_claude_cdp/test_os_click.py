"""
Test: Click REAL del SO (Windows) usando ctypes/PowerShell.
El autofill de DiCloak solo responde a eventos de input del OS, no CDP.
"""
import json, time, sys, os, io, base64, subprocess, ctypes
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

# Primero limpiar el campo (tiene "a" del test anterior)
evaluate("""(() => {
    const pwd = document.querySelector('input[name="Passwd"]');
    if (pwd) { pwd.value = ''; pwd.dispatchEvent(new Event('input', {bubbles:true})); }
})()""")
print(f"Password limpio: {check_password()}")

# Traer pagina al frente
send_cdp("Page.bringToFront")
time.sleep(1)

# Obtener posicion de la ventana del navegador + campo password
# Necesitamos screenX/screenY del browser + rect del campo
window_info = evaluate("""JSON.stringify({
    screenX: window.screenX,
    screenY: window.screenY,
    outerWidth: window.outerWidth,
    outerHeight: window.outerHeight,
    innerWidth: window.innerWidth,
    innerHeight: window.innerHeight,
    devicePixelRatio: window.devicePixelRatio,
})""")
print(f"Window: {window_info}")
win = json.loads(window_info)

field_info = evaluate("""(() => {
    const pwd = document.querySelector('input[name="Passwd"]');
    const rect = pwd.getBoundingClientRect();
    return JSON.stringify({x: rect.x, y: rect.y, w: rect.width, h: rect.height,
        cx: rect.x + rect.width/2, cy: rect.y + rect.height/2});
})()""")
print(f"Field: {field_info}")
field = json.loads(field_info)

# Calcular coordenadas absolutas de pantalla
# La barra de titulo/chrome del navegador ~ diferencia entre outer y inner height
chrome_height = win["outerHeight"] - win["innerHeight"]
screen_x = int(win["screenX"] + field["cx"])
screen_y = int(win["screenY"] + chrome_height + field["cy"])

print(f"\nCoordenadas de pantalla: ({screen_x}, {screen_y})")
print(f"Chrome height (toolbar): {chrome_height}")

# Click REAL usando ctypes (Windows API)
print("\n=== Click REAL con ctypes SetCursorPos + mouse_event ===")

# Mover cursor
ctypes.windll.user32.SetCursorPos(screen_x, screen_y)
time.sleep(0.5)

screenshot("os_before_click.png")

# Click (MOUSEEVENTF_LEFTDOWN = 0x02, MOUSEEVENTF_LEFTUP = 0x04)
ctypes.windll.user32.mouse_event(0x02, 0, 0, 0, 0)  # left down
time.sleep(0.05)
ctypes.windll.user32.mouse_event(0x04, 0, 0, 0, 0)  # left up
time.sleep(2)

screenshot("os_after_click.png")
val = check_password()
print(f"  Password length: {val}")

if not val or int(val) == 0:
    # Intentar doble click OS
    print("\n=== Doble click OS ===")
    ctypes.windll.user32.mouse_event(0x02, 0, 0, 0, 0)
    time.sleep(0.05)
    ctypes.windll.user32.mouse_event(0x04, 0, 0, 0, 0)
    time.sleep(0.1)
    ctypes.windll.user32.mouse_event(0x02, 0, 0, 0, 0)
    time.sleep(0.05)
    ctypes.windll.user32.mouse_event(0x04, 0, 0, 0, 0)
    time.sleep(3)

    screenshot("os_after_dblclick.png")
    val = check_password()
    print(f"  Password length: {val}")

print(f"\n=== RESULTADO: password length = {check_password()} ===")
ws.close()
