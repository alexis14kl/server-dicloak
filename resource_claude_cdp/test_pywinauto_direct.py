"""Test directo: pywinauto click en password page (sin navegar)."""
import json, time, sys, os, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import websockets.sync.client as ws_sync
import urllib.request

port = int(sys.argv[1]) if len(sys.argv) > 1 else 58454

targets = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=3).read())
page = next((t for t in targets if t.get("type") == "page"), None)
print(f"Pagina: {page['title'][:60]}")

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

def check_password():
    return evaluate("document.querySelector('input[name=\"Passwd\"]')?.value.length || 0")

# Info ventana y campo
win_info = json.loads(evaluate("""JSON.stringify({
    screenX: window.screenX, screenY: window.screenY,
    outerH: window.outerHeight, innerH: window.innerHeight,
})"""))

field = json.loads(evaluate("""(() => {
    const pwd = document.querySelector('input[name="Passwd"]');
    const rect = pwd.getBoundingClientRect();
    return JSON.stringify({
        cx: Math.round(rect.x + rect.width/2),
        cy: Math.round(rect.y + rect.height/2),
        bottom: Math.round(rect.y + rect.height),
    });
})()"""))

chrome_h = win_info["outerH"] - win_info["innerH"]
client_x = field["cx"]
client_y = chrome_h + field["cy"]
tooltip_y = chrome_h + field["bottom"] + 25

print(f"Password antes: {check_password()}")
print(f"Client coords: pwd=({client_x}, {client_y}), tooltip=({client_x}, {tooltip_y})")

# Buscar ventana
from pywinauto import Desktop
desktop = Desktop(backend="win32")
browser_win = None
for w in desktop.windows():
    try:
        if w.class_name() == "Chrome_WidgetWin_1" and w.window_text():
            rect = w.rectangle()
            if abs(rect.left - win_info["screenX"]) < 50:
                browser_win = w
                break
    except Exception:
        continue

if not browser_win:
    for w in desktop.windows():
        try:
            if w.class_name() == "Chrome_WidgetWin_1" and w.window_text():
                browser_win = w
                break
        except Exception:
            continue

if not browser_win:
    print("No se encontro ventana!")
    ws.close()
    sys.exit(1)

print(f"Ventana: '{browser_win.window_text()[:50]}' hwnd={browser_win.handle}")

# === Test A: click() — PostMessage, NO mueve cursor ===
print("\n=== Test A: click() [NO mueve cursor] ===")
browser_win.click(coords=(client_x, client_y))
time.sleep(3)
val = check_password()
print(f"  Despues de click en campo: password={val}")

if not val or int(val) == 0:
    browser_win.click(coords=(client_x, tooltip_y))
    time.sleep(3)
    val = check_password()
    print(f"  Despues de click en tooltip: password={val}")

if val and int(val) > 0:
    print(f"\n>>> EXITO SIN MOVER CURSOR! ({val} chars)")
else:
    # === Test B: click_input() — SI mueve cursor ===
    print("\n=== Test B: click_input() [SI mueve cursor] ===")
    browser_win.click_input(coords=(client_x, client_y))
    time.sleep(3)
    val = check_password()
    print(f"  Despues de click_input en campo: password={val}")

    if not val or int(val) == 0:
        browser_win.click_input(coords=(client_x, tooltip_y))
        time.sleep(3)
        val = check_password()
        print(f"  Despues de click_input en tooltip: password={val}")

    if val and int(val) > 0:
        print(f"\n>>> Funciono con click_input ({val} chars) — mueve cursor")
    else:
        print("\n>>> Ninguno funciono")

print(f"\nRESULTADO FINAL: password length = {check_password()}")
ws.close()
