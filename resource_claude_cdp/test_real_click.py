"""
Test: Click REAL (Input.dispatchMouseEvent) en campo password.
DiCloak autofill solo responde a clicks reales del mouse, no a element.click() de JS.
"""
import json, time, sys, os, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import websockets.sync.client as ws_sync
import urllib.request

port = int(sys.argv[1]) if len(sys.argv) > 1 else 55377

# 1. Obtener WebSocket URL
targets = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=3).read())
page = next((t for t in targets if t.get("type") == "page"), None)
if not page:
    print("No hay pagina abierta")
    sys.exit(1)

ws_url = page["webSocketDebuggerUrl"]
print(f"Conectando a: {page['title'][:60]}")
print(f"URL: {page['url'][:80]}")

ws = ws_sync.connect(ws_url, max_size=2**22)
msg_id = 0

def send_cdp(method, params=None):
    global msg_id
    msg_id += 1
    msg = {"id": msg_id, "method": method, "params": params or {}}
    ws.send(json.dumps(msg))
    resp = json.loads(ws.recv(timeout=10))
    return resp

def evaluate(expr):
    r = send_cdp("Runtime.evaluate", {"expression": expr, "returnByValue": True})
    result = r.get("result", {}).get("result", {})
    return result.get("value")

# 2. Ver estado actual del campo password
print("\n=== Estado del campo password ===")
info = evaluate("""(() => {
    const pwd = document.querySelector('input[name="Passwd"]');
    if (!pwd) return JSON.stringify({error: 'NO_PASSWORD_FIELD'});
    const rect = pwd.getBoundingClientRect();
    return JSON.stringify({
        visible: pwd.offsetWidth > 0,
        width: pwd.offsetWidth,
        height: pwd.offsetHeight,
        x: rect.x, y: rect.y,
        centerX: rect.x + rect.width/2,
        centerY: rect.y + rect.height/2,
        value_length: pwd.value.length,
        focused: document.activeElement === pwd,
    });
})()""")
print(info)

coords = json.loads(info)
if "error" in coords:
    print("No se encontro campo password")
    ws.close()
    sys.exit(1)

cx = coords["centerX"]
cy = coords["centerY"]
print(f"\nCentro del campo: ({cx}, {cy})")

# 3. Click REAL usando Input.dispatchMouseEvent
print("\n=== Click REAL con Input.dispatchMouseEvent ===")

# mousePressed
r1 = send_cdp("Input.dispatchMouseEvent", {
    "type": "mousePressed",
    "x": cx, "y": cy,
    "button": "left",
    "clickCount": 1,
})
print(f"mousePressed: {r1.get('result', r1.get('error', 'ok'))}")

time.sleep(0.1)

# mouseReleased
r2 = send_cdp("Input.dispatchMouseEvent", {
    "type": "mouseReleased",
    "x": cx, "y": cy,
    "button": "left",
    "clickCount": 1,
})
print(f"mouseReleased: {r2.get('result', r2.get('error', 'ok'))}")

time.sleep(2)

# 4. Ver si DiCloak mostro tooltip
print("\n=== Buscando tooltip/popup de DiCloak ===")
popup = evaluate("""(() => {
    const result = {
        activeElement: document.activeElement?.tagName + '#' + (document.activeElement?.name || ''),
        passwordValue: document.querySelector('input[name="Passwd"]')?.value.length || 0,
    };

    // Buscar elementos nuevos que parezcan tooltip de extension
    const allDivs = document.querySelectorAll('div, iframe, [class*="autofill"], [class*="tooltip"], [class*="popup"], [class*="suggest"]');
    const visible = Array.from(allDivs).filter(el => {
        const style = window.getComputedStyle(el);
        const rect = el.getBoundingClientRect();
        return (style.position === 'absolute' || style.position === 'fixed')
            && rect.width > 30 && rect.height > 20
            && style.display !== 'none' && style.visibility !== 'hidden'
            && rect.y > 0;
    });

    result.visiblePopups = visible.map(el => ({
        tag: el.tagName,
        class: (el.className || '').toString().substring(0, 60),
        text: (el.innerText || '').substring(0, 100),
        rect: {x: Math.round(el.getBoundingClientRect().x), y: Math.round(el.getBoundingClientRect().y),
               w: Math.round(el.getBoundingClientRect().width), h: Math.round(el.getBoundingClientRect().height)},
        zIndex: window.getComputedStyle(el).zIndex,
    })).slice(0, 10);

    // Buscar shadow DOMs (extensiones inyectan en shadow DOM)
    const shadows = [];
    document.querySelectorAll('*').forEach(el => {
        if (el.shadowRoot) {
            shadows.push({
                tag: el.tagName,
                id: el.id,
                class: (el.className || '').toString().substring(0, 40),
                shadowChildren: el.shadowRoot.childElementCount,
                shadowText: (el.shadowRoot.textContent || '').substring(0, 100),
            });
        }
    });
    result.shadowDOMs = shadows;

    // Buscar iframes de extension
    const iframes = Array.from(document.querySelectorAll('iframe')).map(f => ({
        src: (f.src || '').substring(0, 100),
        visible: f.offsetWidth > 0,
        rect: f.offsetWidth > 0 ? {
            x: Math.round(f.getBoundingClientRect().x),
            y: Math.round(f.getBoundingClientRect().y),
            w: Math.round(f.getBoundingClientRect().width),
            h: Math.round(f.getBoundingClientRect().height),
        } : null,
    }));
    result.iframes = iframes;

    return JSON.stringify(result, null, 2);
})()""")
print(popup)

# 5. Intentar doble click real tambien
print("\n=== Doble click REAL ===")
send_cdp("Input.dispatchMouseEvent", {"type": "mousePressed", "x": cx, "y": cy, "button": "left", "clickCount": 2})
time.sleep(0.05)
send_cdp("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": cx, "y": cy, "button": "left", "clickCount": 2})

time.sleep(2)

popup2 = evaluate("""(() => {
    const pwd = document.querySelector('input[name="Passwd"]');
    const result = {
        passwordValue: pwd?.value.length || 0,
        focused: document.activeElement === pwd,
    };

    // Buscar shadow DOMs nuevos
    const shadows = [];
    document.querySelectorAll('*').forEach(el => {
        if (el.shadowRoot) {
            shadows.push({
                tag: el.tagName, id: el.id,
                class: (el.className || '').toString().substring(0, 40),
                shadowText: (el.shadowRoot.textContent || '').substring(0, 150),
            });
        }
    });
    result.shadowDOMs = shadows;

    return JSON.stringify(result, null, 2);
})()""")
print(popup2)

ws.close()
print("\nDone.")
