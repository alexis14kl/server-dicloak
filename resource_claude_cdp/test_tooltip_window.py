"""
Test: Click directo en la ventana del tooltip (Chrome_WidgetWin_4).
"""
import json, time, sys, os, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import websockets.sync.client as ws_sync
import urllib.request
from pywinauto import Desktop
import pywinauto.controls.hwndwrapper as hw
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

# Limpiar
evaluate("""(() => {
    const pwd = document.querySelector('input[name="Passwd"]');
    if (pwd) { pwd.value = ''; pwd.dispatchEvent(new Event('input', {bubbles:true})); }
})()""")

# Info
win_info = json.loads(evaluate("""JSON.stringify({
    screenX: window.screenX, screenY: window.screenY,
    outerH: window.outerHeight, innerH: window.innerHeight,
})"""))
field = json.loads(evaluate("""(() => {
    const pwd = document.querySelector('input[name="Passwd"]');
    const rect = pwd.getBoundingClientRect();
    return JSON.stringify({cx: Math.round(rect.x + rect.width/2), cy: Math.round(rect.y + rect.height/2)});
})()"""))

chrome_h = win_info["outerH"] - win_info["innerH"]
client_x = field["cx"]
client_y = chrome_h + field["cy"]

# Buscar browser
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

print(f"Browser: '{browser_win.window_text()[:40]}' hwnd={browser_win.handle}")
print(f"Password antes: {check_password()}")

# Ventanas antes
before_hwnds = set()
def get_visible_hwnds():
    hwnds = set()
    def cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            hwnds.add(hwnd)
        return True
    win32gui.EnumWindows(cb, None)
    return hwnds

before_hwnds = get_visible_hwnds()

# 1. Click en campo password (abre tooltip)
print("\n=== Click pywinauto en campo password ===")
browser_win.click(coords=(client_x, client_y))
time.sleep(2)

# 2. Buscar ventana nueva del tooltip
after_hwnds = get_visible_hwnds()
new_hwnds = after_hwnds - before_hwnds

tooltip_win = None
for hwnd in new_hwnds:
    cls = win32gui.GetClassName(hwnd)
    rect = win32gui.GetWindowRect(hwnd)
    w = rect[2] - rect[0]
    h = rect[3] - rect[1]
    title = win32gui.GetWindowText(hwnd)
    print(f"  Nueva ventana: hwnd={hwnd} class='{cls}' title='{title}' "
          f"rect={rect} size={w}x{h}")
    if "Chrome_WidgetWin" in cls and w > 50 and h > 30:
        tooltip_win = hwnd
        print(f"  >>> Tooltip encontrado!")

if not tooltip_win:
    print("  No se encontro tooltip! Buscando Chrome_WidgetWin_4...")
    def cb2(hwnd, results):
        cls = win32gui.GetClassName(hwnd)
        if "Chrome_WidgetWin_4" in cls and win32gui.IsWindowVisible(hwnd):
            results.append(hwnd)
        return True
    results = []
    win32gui.EnumWindows(cb2, results)
    if results:
        tooltip_win = results[0]
        rect = win32gui.GetWindowRect(tooltip_win)
        print(f"  Encontrado: hwnd={tooltip_win} rect={rect}")

if tooltip_win:
    rect = win32gui.GetWindowRect(tooltip_win)
    tw = rect[2] - rect[0]
    th = rect[3] - rect[1]
    print(f"\n=== Click en tooltip ventana hwnd={tooltip_win} size={tw}x{th} ===")

    tooltip_ctrl = hw.HwndWrapper(tooltip_win)

    # Click en el centro del tooltip
    cx = tw // 2
    cy = th // 2
    print(f"  Click en centro: ({cx}, {cy})")
    tooltip_ctrl.click(coords=(cx, cy))
    time.sleep(2)

    val = check_password()
    print(f"  Password: {val}")

    if not val or int(val) == 0:
        # Intentar en la parte superior (primer item del dropdown)
        print(f"  Click en parte superior: ({cx}, 30)")
        # Re-abrir tooltip
        browser_win.click(coords=(client_x, client_y))
        time.sleep(1.5)

        # Buscar tooltip de nuevo
        after2 = get_visible_hwnds()
        new2 = after2 - before_hwnds
        for hwnd in new2:
            cls = win32gui.GetClassName(hwnd)
            if "Chrome_WidgetWin" in cls:
                tooltip_win = hwnd
                break

        tooltip_ctrl = hw.HwndWrapper(tooltip_win)
        rect = win32gui.GetWindowRect(tooltip_win)
        tw = rect[2] - rect[0]
        th = rect[3] - rect[1]

        # Intentar diferentes posiciones Y
        for y_pos in [20, 40, 60, 80, th//3, th//2]:
            tooltip_ctrl.click(coords=(tw//2, y_pos))
            time.sleep(1)
            val = check_password()
            print(f"    y={y_pos}: password={val}")
            if val and int(val) > 0:
                print(f"    >>> FUNCIONO en y={y_pos}!")
                break
            # Re-abrir si se cerro
            if not win32gui.IsWindowVisible(tooltip_win):
                browser_win.click(coords=(client_x, client_y))
                time.sleep(1.5)
                after3 = get_visible_hwnds()
                new3 = after3 - before_hwnds
                for hwnd in new3:
                    cls = win32gui.GetClassName(hwnd)
                    if "Chrome_WidgetWin" in cls:
                        tooltip_win = hwnd
                        tooltip_ctrl = hw.HwndWrapper(tooltip_win)
                        rect = win32gui.GetWindowRect(tooltip_win)
                        tw = rect[2] - rect[0]
                        th = rect[3] - rect[1]
                        break
else:
    print("  No se encontro ventana tooltip")

print(f"\n=== RESULTADO FINAL: password = {check_password()} ===")
ws.close()
