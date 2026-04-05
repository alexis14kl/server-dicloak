"""
Test: Click en password field usando pywinauto (sin mover cursor fisico).
pywinauto envia WM_LBUTTONDOWN/UP via PostMessage directo a la ventana.
"""
import json, time, sys, os, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import websockets.sync.client as ws_sync
import urllib.request

port = int(sys.argv[1]) if len(sys.argv) > 1 else 57743

# 1. Conectar CDP
targets = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=3).read())
page = next((t for t in targets if t.get("type") == "page"), None)
if not page:
    print("No hay pagina")
    sys.exit(1)

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

def check_password():
    return evaluate("document.querySelector('input[name=\"Passwd\"]')?.value.length || 0")

# 2. Verificar que estamos en pagina de password
url = evaluate("window.location.href") or ""
if "challenge/pwd" not in url:
    print(f"No estamos en pagina de password. URL: {url[:80]}")
    print("Necesitas estar en la pagina de contrasena de Google para este test.")
    ws.close()
    sys.exit(1)

# 3. Obtener info de la ventana y campo
win_info = json.loads(evaluate("""JSON.stringify({
    screenX: window.screenX, screenY: window.screenY,
    outerH: window.outerHeight, innerH: window.innerHeight,
    outerW: window.outerWidth,
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
print(f"\nWindow: screenX={win_info['screenX']}, screenY={win_info['screenY']}, chrome_h={chrome_h}")
print(f"Campo password (viewport): cx={field['cx']}, cy={field['cy']}")
print(f"Password antes: {check_password()}")

# 4. Buscar la ventana del navegador con pywinauto
print("\n=== Buscando ventana con pywinauto ===")
from pywinauto import Desktop
import pywinauto

# Buscar ventana por titulo parcial
desktop = Desktop(backend="win32")
title_part = evaluate("document.title") or "Google"
print(f"Buscando ventana con titulo que contenga: '{title_part[:30]}'")

# Listar ventanas para debug
windows = desktop.windows()
browser_win = None
for w in windows:
    try:
        t = w.window_text()
        if title_part[:15] in t or "ginsbrowser" in t.lower() or "dicloak" in t.lower():
            print(f"  Encontrada: '{t[:60]}' class={w.class_name()}")
            browser_win = w
            break
    except Exception:
        continue

if not browser_win:
    # Fallback: buscar por clase de Chromium
    for w in windows:
        try:
            if w.class_name() == "Chrome_WidgetWin_1":
                t = w.window_text()
                print(f"  Chrome window: '{t[:60]}'")
                if t:  # Ignorar ventanas sin titulo
                    browser_win = w
                    break
        except Exception:
            continue

if not browser_win:
    print("No se encontro la ventana del navegador!")
    ws.close()
    sys.exit(1)

print(f"\nVentana encontrada: '{browser_win.window_text()[:60]}'")
print(f"  HWND: {browser_win.handle}")
rect = browser_win.rectangle()
print(f"  Rect: left={rect.left}, top={rect.top}, w={rect.width()}, h={rect.height()}")

# 5. Calcular coordenadas relativas a la ventana (client area)
# El campo en viewport coords + chrome_h = coords relativas a la ventana
client_x = field["cx"]
client_y = chrome_h + field["cy"]
tooltip_client_y = chrome_h + field["bottom"] + 25

print(f"\nCoords cliente: password=({client_x}, {client_y}), tooltip=({client_x}, {tooltip_client_y})")

# 6. Click pywinauto en campo password (sin mover cursor)
print("\n=== Click pywinauto en campo password ===")
try:
    browser_win.click(coords=(client_x, client_y))
    print("  Click enviado (sin mover cursor)")
    time.sleep(2)

    val = check_password()
    print(f"  Password length: {val}")

    if not val or int(val) == 0:
        # 7. Click en tooltip
        print("\n=== Click pywinauto en tooltip ===")
        browser_win.click(coords=(client_x, tooltip_client_y))
        print("  Click tooltip enviado")
        time.sleep(2)

        val = check_password()
        print(f"  Password length: {val}")

        if not val or int(val) == 0:
            # Intentar click_input (este SI mueve cursor pero es mas confiable)
            print("\n=== Fallback: click_input (mueve cursor) ===")
            browser_win.click_input(coords=(client_x, client_y))
            time.sleep(2)
            val = check_password()
            print(f"  Password length despues de click_input: {val}")

            if not val or int(val) == 0:
                browser_win.click_input(coords=(client_x, tooltip_client_y))
                time.sleep(2)
                val = check_password()
                print(f"  Password length despues de tooltip click_input: {val}")

except Exception as e:
    print(f"  Error: {e}")

print(f"\n=== RESULTADO: password length = {check_password()} ===")
ws.close()
