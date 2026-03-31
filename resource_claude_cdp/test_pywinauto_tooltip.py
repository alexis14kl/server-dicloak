"""
Test: pywinauto click() en password + buscar tooltip como ventana separada.
"""
import json, time, sys, os, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import websockets.sync.client as ws_sync
import urllib.request
from pywinauto import Desktop
import win32gui

port = int(sys.argv[1]) if len(sys.argv) > 1 else 58454

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

def check_password():
    return evaluate("document.querySelector('input[name=\"Passwd\"]')?.value.length || 0")

# Limpiar password si tiene algo
evaluate("""(() => {
    const pwd = document.querySelector('input[name="Passwd"]');
    if (pwd) { pwd.value = ''; pwd.dispatchEvent(new Event('input', {bubbles:true})); }
})()""")

# Info ventana
win_info = json.loads(evaluate("""JSON.stringify({
    screenX: window.screenX, screenY: window.screenY,
    outerH: window.outerHeight, innerH: window.innerHeight,
})"""))
field = json.loads(evaluate("""(() => {
    const pwd = document.querySelector('input[name="Passwd"]');
    const rect = pwd.getBoundingClientRect();
    return JSON.stringify({cx: Math.round(rect.x + rect.width/2), cy: Math.round(rect.y + rect.height/2),
        bottom: Math.round(rect.y + rect.height), x: Math.round(rect.x), w: Math.round(rect.width)});
})()"""))

chrome_h = win_info["outerH"] - win_info["innerH"]
client_x = field["cx"]
client_y = chrome_h + field["cy"]

print(f"Password antes: {check_password()}")

# Buscar ventana del browser
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
    print("No se encontro ventana!")
    sys.exit(1)

print(f"Browser: '{browser_win.window_text()[:40]}' hwnd={browser_win.handle}")

# Listar todas las ventanas ANTES del click (para comparar)
def list_all_windows():
    windows = []
    def enum_callback(hwnd, results):
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd)
            cls = win32gui.GetClassName(hwnd)
            rect = win32gui.GetWindowRect(hwnd)
            w = rect[2] - rect[0]
            h = rect[3] - rect[1]
            if w > 10 and h > 10:
                results.append({"hwnd": hwnd, "title": title[:50], "class": cls,
                    "rect": rect, "w": w, "h": h})
        return True
    win32gui.EnumWindows(enum_callback, windows)
    return windows

print("\n=== Ventanas ANTES del click ===")
windows_before = list_all_windows()
hwnds_before = set(w["hwnd"] for w in windows_before)
print(f"  Total ventanas visibles: {len(windows_before)}")

# === CLICK en campo password ===
print("\n=== Click pywinauto en campo password ===")
browser_win.click(coords=(client_x, client_y))
time.sleep(2)

# Listar ventanas DESPUES del click
print("\n=== Ventanas DESPUES del click (nuevas) ===")
windows_after = list_all_windows()
new_windows = [w for w in windows_after if w["hwnd"] not in hwnds_before]

if new_windows:
    for w in new_windows:
        print(f"  NUEVA: hwnd={w['hwnd']} class='{w['class']}' title='{w['title']}' "
              f"rect={w['rect']} size={w['w']}x{w['h']}")
else:
    print("  No aparecieron ventanas nuevas")
    # Buscar ventanas Chrome_WidgetWin que cambiaron de tamaño o aparecieron
    chrome_wins = [w for w in windows_after if "Chrome" in w["class"]]
    print(f"  Ventanas Chrome_*: {len(chrome_wins)}")
    for w in chrome_wins:
        print(f"    hwnd={w['hwnd']} class='{w['class']}' title='{w['title'][:30]}' "
              f"pos=({w['rect'][0]},{w['rect'][1]}) size={w['w']}x{w['h']}")

# Buscar hijos de la ventana del browser
print("\n=== Ventanas hijas del browser ===")
child_windows = []
def enum_child(hwnd, results):
    cls = win32gui.GetClassName(hwnd)
    title = win32gui.GetWindowText(hwnd)
    rect = win32gui.GetWindowRect(hwnd)
    visible = win32gui.IsWindowVisible(hwnd)
    w = rect[2] - rect[0]
    h = rect[3] - rect[1]
    if visible and (w > 5 or h > 5):
        results.append({"hwnd": hwnd, "class": cls, "title": title[:40],
            "rect": rect, "w": w, "h": h})
    return True

win32gui.EnumChildWindows(browser_win.handle, enum_child, child_windows)
print(f"  Hijos visibles: {len(child_windows)}")
for c in child_windows:
    print(f"    hwnd={c['hwnd']} class='{c['class']}' size={c['w']}x{c['h']} "
          f"pos=({c['rect'][0]},{c['rect'][1]})")

# Intentar click en posibles tooltips
val = check_password()
print(f"\nPassword despues de click en campo: {val}")

if not val or int(val) == 0:
    # El tooltip podria ser una ventana hija o popup del browser
    # Intentar click en diferentes posiciones cerca del campo
    tooltip_positions = [
        (client_x, chrome_h + field["bottom"] + 15),
        (client_x, chrome_h + field["bottom"] + 25),
        (client_x, chrome_h + field["bottom"] + 35),
        (client_x, chrome_h + field["bottom"] + 50),
        (field["x"] + 50, chrome_h + field["bottom"] + 25),  # mas a la izquierda
    ]

    for i, (tx, ty) in enumerate(tooltip_positions):
        print(f"\n  Tooltip intento {i+1}: ({tx}, {ty})")
        # Primero re-click en campo para que reaparezca tooltip
        if i > 0:
            browser_win.click(coords=(client_x, client_y))
            time.sleep(1.5)

        browser_win.click(coords=(tx, ty))
        time.sleep(1.5)

        val = check_password()
        print(f"    Password: {val}")
        if val and int(val) > 0:
            print(f"    >>> FUNCIONO en posicion ({tx}, {ty})!")
            break

    if not val or int(val) == 0:
        # Ultimo intento: click en ventanas hijas directamente
        print("\n  Intentando click en ventanas hijas...")
        for c in child_windows:
            if c["w"] > 50 and c["h"] > 20:
                try:
                    import pywinauto.controls.hwndwrapper as hw
                    child_ctrl = hw.HwndWrapper(c["hwnd"])
                    child_ctrl.click(coords=(c["w"]//2, c["h"]//2))
                    time.sleep(1)
                    val = check_password()
                    if val and int(val) > 0:
                        print(f"    >>> Click en hijo hwnd={c['hwnd']} funciono!")
                        break
                except Exception as e:
                    pass

print(f"\n=== RESULTADO FINAL: password = {check_password()} ===")
ws.close()
