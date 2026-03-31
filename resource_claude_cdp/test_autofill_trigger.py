"""
Test: Intentar activar el autofill de DiCloak con diferentes estrategias.
"""
import json, time, sys, os, io, base64
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
    resp = json.loads(ws.recv(timeout=15))
    return resp

def evaluate(expr):
    r = send_cdp("Runtime.evaluate", {"expression": expr, "returnByValue": True})
    return r.get("result", {}).get("result", {}).get("value")

def screenshot(name):
    r = send_cdp("Page.captureScreenshot", {"format": "png"})
    data = r.get("result", {}).get("data", "")
    if data:
        out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "screenshots")
        os.makedirs(out, exist_ok=True)
        path = os.path.join(out, name)
        with open(path, "wb") as f:
            f.write(base64.b64decode(data))
        print(f"  Screenshot: {name}")

def check_password():
    return evaluate("document.querySelector('input[name=\"Passwd\"]')?.value.length || 0")

# Coords del campo password
coords = json.loads(evaluate("""(() => {
    const pwd = document.querySelector('input[name="Passwd"]');
    const rect = pwd.getBoundingClientRect();
    return JSON.stringify({cx: rect.x + rect.width/2, cy: rect.y + rect.height/2,
        left: rect.x + 5, top: rect.y + 5});
})()"""))
cx, cy = coords["cx"], coords["cy"]

print(f"Password field centro: ({cx:.0f}, {cy:.0f})")
print(f"Password length inicial: {check_password()}")

# ========================================
# Estrategia 1: Page.bringToFront + mouseMoved + click
# ========================================
print("\n=== Estrategia 1: bringToFront + mouseMoved + click ===")
send_cdp("Page.bringToFront")
time.sleep(0.5)

# Mover mouse desde lejos hasta el campo
for step_y in range(100, int(cy), 50):
    send_cdp("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": cx, "y": step_y})
    time.sleep(0.02)

send_cdp("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": cx, "y": cy})
time.sleep(0.3)

# Click
send_cdp("Input.dispatchMouseEvent", {"type": "mousePressed", "x": cx, "y": cy, "button": "left", "clickCount": 1})
time.sleep(0.05)
send_cdp("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": cx, "y": cy, "button": "left", "clickCount": 1})
time.sleep(2)

val = check_password()
print(f"  Password length: {val}")
screenshot("strat1_after_click.png")
if val and int(val) > 0:
    print("  >>> AUTOFILL FUNCIONO!")

# ========================================
# Estrategia 2: Click afuera, luego click en password (simula usuario)
# ========================================
if not val or int(val) == 0:
    print("\n=== Estrategia 2: Click afuera + click en password ===")
    # Click en area vacia
    send_cdp("Input.dispatchMouseEvent", {"type": "mousePressed", "x": 500, "y": 500, "button": "left", "clickCount": 1})
    time.sleep(0.05)
    send_cdp("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": 500, "y": 500, "button": "left", "clickCount": 1})
    time.sleep(1)

    # Ahora click en password
    send_cdp("Input.dispatchMouseEvent", {"type": "mousePressed", "x": cx, "y": cy, "button": "left", "clickCount": 1})
    time.sleep(0.05)
    send_cdp("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": cx, "y": cy, "button": "left", "clickCount": 1})
    time.sleep(2)

    val = check_password()
    print(f"  Password length: {val}")
    screenshot("strat2_after_click.png")

# ========================================
# Estrategia 3: Doble click real
# ========================================
if not val or int(val) == 0:
    print("\n=== Estrategia 3: Doble click real ===")
    send_cdp("Input.dispatchMouseEvent", {"type": "mousePressed", "x": cx, "y": cy, "button": "left", "clickCount": 1})
    time.sleep(0.05)
    send_cdp("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": cx, "y": cy, "button": "left", "clickCount": 1})
    time.sleep(0.1)
    send_cdp("Input.dispatchMouseEvent", {"type": "mousePressed", "x": cx, "y": cy, "button": "left", "clickCount": 2})
    time.sleep(0.05)
    send_cdp("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": cx, "y": cy, "button": "left", "clickCount": 2})
    time.sleep(2)

    val = check_password()
    print(f"  Password length: {val}")
    screenshot("strat3_after_dblclick.png")

# ========================================
# Estrategia 4: navigator.credentials.get()
# ========================================
if not val or int(val) == 0:
    print("\n=== Estrategia 4: navigator.credentials.get() ===")
    cred = evaluate("""(async () => {
        try {
            const cred = await navigator.credentials.get({
                password: true,
                mediation: 'optional'
            });
            if (cred) {
                return JSON.stringify({type: cred.type, id: cred.id, hasPassword: !!cred.password, nameLen: cred.name?.length});
            }
            return 'NO_CREDENTIAL';
        } catch(e) {
            return 'ERROR: ' + e.message;
        }
    })()""")
    # Necesita awaitPromise
    r = send_cdp("Runtime.evaluate", {
        "expression": """(async () => {
            try {
                const cred = await navigator.credentials.get({password: true, mediation: 'optional'});
                if (cred) {
                    // Intentar llenar el campo
                    const pwd = document.querySelector('input[name="Passwd"]');
                    if (pwd && cred.password) {
                        pwd.focus();
                        pwd.value = cred.password;
                        pwd.dispatchEvent(new Event('input', {bubbles: true}));
                        pwd.dispatchEvent(new Event('change', {bubbles: true}));
                        return 'FILLED:' + cred.password.length;
                    }
                    return JSON.stringify({type: cred.type, id: cred.id, passLen: cred.password?.length});
                }
                return 'NO_CREDENTIAL';
            } catch(e) {
                return 'ERROR: ' + e.message;
            }
        })()""",
        "returnByValue": True,
        "awaitPromise": True,
        "timeout": 5000,
    })
    cred_val = r.get("result", {}).get("result", {}).get("value", str(r))
    print(f"  Credentials: {cred_val}")

    val = check_password()
    print(f"  Password length: {val}")

# ========================================
# Estrategia 5: Input.insertText (escribir caracter para triggear autofill)
# ========================================
if not val or int(val) == 0:
    print("\n=== Estrategia 5: Escribir un caracter + borrar (trigger autofill) ===")
    evaluate("document.querySelector('input[name=\"Passwd\"]').focus()")
    time.sleep(0.3)

    # Escribir un caracter
    send_cdp("Input.insertText", {"text": "a"})
    time.sleep(1)
    screenshot("strat5_after_type.png")

    # Borrar
    send_cdp("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Backspace", "code": "Backspace", "windowsVirtualKeyCode": 8})
    send_cdp("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Backspace", "code": "Backspace", "windowsVirtualKeyCode": 8})
    time.sleep(1)

    val = check_password()
    print(f"  Password length: {val}")
    screenshot("strat5_final.png")

# ========================================
# Estrategia 6: Tab desde otro campo (navegar con Tab activa autofill a veces)
# ========================================
if not val or int(val) == 0:
    print("\n=== Estrategia 6: Tab navigation ===")
    # Focus en checkbox primero
    evaluate("document.querySelector('input[type=\"checkbox\"]')?.focus()")
    time.sleep(0.3)

    # Shift+Tab para ir al password
    send_cdp("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Tab", "code": "Tab", "windowsVirtualKeyCode": 9, "modifiers": 8})
    send_cdp("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Tab", "code": "Tab", "windowsVirtualKeyCode": 9, "modifiers": 8})
    time.sleep(2)

    val = check_password()
    print(f"  Password length: {val}")
    screenshot("strat6_tab.png")

print(f"\n=== RESULTADO FINAL: password length = {check_password()} ===")
ws.close()
