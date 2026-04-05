"""Navegar a Flow, pasar account chooser, llegar a password page, probar pywinauto."""
import json, time, sys, os, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import websockets.sync.client as ws_sync
import urllib.request

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

# 1. Navegar a Flow
print("1. Navegando a Flow...")
send_cdp("Page.navigate", {"url": "https://labs.google/fx/tools/flow"})
time.sleep(5)

url = evaluate("window.location.href") or ""
print(f"   URL: {url[:80]}")

# 2. Si estamos en account chooser, click en cuenta
if "accountchooser" in url.lower():
    print("2. Account chooser detectado. Click en cuenta...")
    r = evaluate("""(() => {
        const byId = document.querySelector('[data-identifier]');
        if (byId) { byId.click(); return 'data-identifier: ' + byId.getAttribute('data-identifier'); }
        return 'NO_ACCOUNT';
    })()""")
    print(f"   {r}")
    time.sleep(5)
    url = evaluate("window.location.href") or ""
    print(f"   URL: {url[:80]}")

# 3. Verificar si estamos en password page
if "challenge/pwd" in url.lower():
    print("\n3. EN PAGINA DE PASSWORD! Probando pywinauto...")

    # Obtener info
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
    tooltip_client_y = chrome_h + field["bottom"] + 25

    print(f"   Window: screenX={win_info['screenX']}, chrome_h={chrome_h}")
    print(f"   Client coords: password=({client_x}, {client_y}), tooltip=({client_x}, {tooltip_client_y})")
    print(f"   Password antes: {check_password()}")

    # pywinauto
    from pywinauto import Desktop
    desktop = Desktop(backend="win32")

    # Buscar ventana ginsbrowser/Chrome
    browser_win = None
    for w in desktop.windows():
        try:
            cn = w.class_name()
            if cn == "Chrome_WidgetWin_1" and w.window_text():
                t = w.window_text()
                print(f"   Chrome window: '{t[:50]}' hwnd={w.handle}")
                # Verificar que es la ventana correcta por posicion
                rect = w.rectangle()
                if abs(rect.left - win_info["screenX"]) < 50:
                    browser_win = w
                    print(f"   >>> Match por posicion!")
                    break
        except Exception:
            continue

    if not browser_win:
        # Fallback: primera Chrome_WidgetWin_1 con titulo
        for w in desktop.windows():
            try:
                if w.class_name() == "Chrome_WidgetWin_1" and w.window_text():
                    browser_win = w
                    break
            except Exception:
                continue

    if browser_win:
        print(f"\n   Ventana: '{browser_win.window_text()[:50]}' hwnd={browser_win.handle}")

        # Test A: click() — PostMessage, NO mueve cursor
        print("\n=== Test A: pywinauto click() [NO mueve cursor] ===")
        browser_win.click(coords=(client_x, client_y))
        time.sleep(2)
        val = check_password()
        print(f"   Password: {val}")

        if not val or int(val) == 0:
            # Click en tooltip
            browser_win.click(coords=(client_x, tooltip_client_y))
            time.sleep(2)
            val = check_password()
            print(f"   Password despues tooltip: {val}")

        if val and int(val) > 0:
            print(f"\n   >>> FUNCIONO SIN MOVER CURSOR! ({val} chars)")
        else:
            # Test B: click_input() — SI mueve cursor pero mas confiable
            print("\n=== Test B: pywinauto click_input() [SI mueve cursor] ===")
            browser_win.click_input(coords=(client_x, client_y))
            time.sleep(2)
            val = check_password()
            print(f"   Password: {val}")

            if not val or int(val) == 0:
                browser_win.click_input(coords=(client_x, tooltip_client_y))
                time.sleep(2)
                val = check_password()
                print(f"   Password despues tooltip: {val}")

            if val and int(val) > 0:
                print(f"\n   >>> Funciono con click_input ({val} chars) — mueve cursor")
            else:
                print("\n   >>> Ninguno funciono")
    else:
        print("   No se encontro ventana del navegador")

    print(f"\n=== RESULTADO FINAL: password = {check_password()} ===")
else:
    print(f"\nNo llego a password page. URL: {url[:100]}")

ws.close()
