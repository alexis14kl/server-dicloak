# Cómo habilitar la consola de desarrollo en ginsbrowser (DICloak)

## Contexto

DICloak usa su propio navegador llamado **ginsbrowser** (Chromium-based, v142/143).
Por defecto bloquea el acceso a DevTools vía teclado (F12, Ctrl+Shift+I) a través
de sus extensiones internas (`.ginsextension2`, `.ginsextension3`, `.simulation`).

Sin embargo, ginsbrowser sí expone el protocolo **CDP (Chrome DevTools Protocol)**
en el puerto asignado al perfil abierto (ej. `52101`), lo que permite acceder a
DevTools programáticamente desde el exterior.

---

## Requisitos

| Requisito | Detalle |
|---|---|
| `server.py` corriendo | Puerto 8585 |
| DICloak abierto con CDP | Puerto 9333 (lanzado con scheduled task) |
| Perfil abierto | Ej. `#1 Chat Gpt Pro` → CDP en puerto `52101` |
| Python + `websockets` | `pip install websockets` |

---

## Paso 1 — Lanzar DICloak con CDP (si no está corriendo)

DICloak debe iniciarse con `--remote-debugging-port=9333` en la sesión del usuario.
Desde SSH (Session 0) se usa una **scheduled task interactiva**:

```bat
schtasks /create /tn "DICloak_CDP" ^
  /tr "\"C:\Program Files\DICloak\DICloak.exe\" --remote-debugging-port=9333 --remote-debugging-address=127.0.0.1" ^
  /sc ONCE /st 00:00 /ru NyGsoft /it /f

schtasks /run /tn "DICloak_CDP"
```

Verificar que esté escuchando:
```bat
netstat -ano | findstr LISTENING | findstr 9333
```

---

## Paso 2 — Abrir perfil y obtener puerto CDP del navegador

Via la API del `server.py`:

```bash
# Listar perfiles disponibles
curl http://127.0.0.1:8585/profiles

# Abrir perfil (reemplazar nombre según sea necesario)
curl -X POST http://127.0.0.1:8585/profiles/open \
  -H "Content-Type: application/json" \
  -d '{"name": "#1 Chat Gpt Pro", "timeout": 90}'
```

Respuesta:
```json
{
  "success": true,
  "data": {
    "profile": {
      "name": "#1 Chat Gpt Pro",
      "debug_port": 52101,
      "cdp_active": true
    }
  }
}
```

El campo `debug_port` (ej. `52101`) es el puerto CDP del ginsbrowser de ese perfil.

---

## Paso 3 — Acceder a DevTools en el navegador

### Opción A — Abrir DevTools como pestaña en el mismo ginsbrowser

Usando Python con la librería `websockets`:

```python
import websockets.sync.client as wsc, json, time, urllib.request

CDP_PORT = 52101  # puerto obtenido en Paso 2

# 1. Obtener el PAGE_ID de la pestaña activa (ChatGPT)
targets = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json").read())
page = next(t for t in targets if t.get("type") == "page" and "chatgpt" in t.get("url",""))
PAGE_ID = page["id"]

# 2. Obtener WebSocket del browser target
BROWSER_WS = json.loads(
    urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json/version").read()
)["webSocketDebuggerUrl"]

# 3. Abrir DevTools como nueva pestaña
ws = wsc.connect(BROWSER_WS, max_size=2**23)
DT_URL = f"http://127.0.0.1:{CDP_PORT}/devtools/inspector.html?ws=127.0.0.1:{CDP_PORT}/devtools/page/{PAGE_ID}"
ws.send(json.dumps({"id": 1, "method": "Target.createTarget", "params": {"url": DT_URL}}))

deadline = time.time() + 5
while time.time() < deadline:
    try:
        ws.socket.settimeout(1)
        msg = json.loads(ws.recv())
        if msg.get("id") == 1:
            print("DevTools abierto, target:", msg["result"]["targetId"])
            break
    except:
        pass
ws.close()
```

### Opción B — URL directa en otro navegador (Chrome/Edge en el mismo PC)

Abrir en Chrome/Edge:
```
http://127.0.0.1:52101/devtools/inspector.html?ws=127.0.0.1:52101/devtools/page/{PAGE_ID}
```

---

## Paso 4 — Habilitar F12 / Ctrl+Shift+I dentro del ginsbrowser

Las extensiones de DICloak bloquean estos atajos. Se inyecta un listener via CDP
que los intercepta **antes** que las extensiones (`capture: true`):

```python
import websockets.sync.client as wsc, json, time

CDP_PORT = 52101
PAGE_ID  = "86108406DACE8587F0E91B63B60DA3D5"  # reemplazar con el real

ws = wsc.connect(f"ws://127.0.0.1:{CDP_PORT}/devtools/page/{PAGE_ID}", max_size=2**23)

shortcut_js = f"""
(() => {{
  if (window.__SHORTCUTS_ENABLED__) return;
  window.__SHORTCUTS_ENABLED__ = true;
  document.addEventListener('keydown', e => {{
    const ctrl  = e.ctrlKey || e.metaKey;
    const shift = e.shiftKey;
    if (e.key === 'F12' || (ctrl && shift && e.key === 'I') || (ctrl && shift && e.key === 'J')) {{
      e.stopImmediatePropagation();
      window.open(
        'http://127.0.0.1:{CDP_PORT}/devtools/inspector.html?ws=127.0.0.1:{CDP_PORT}/devtools/page/{PAGE_ID}',
        '_blank'
      );
    }}
  }}, true);
}})()
"""

# Inyección en la página actual
ws.send(json.dumps({"id":1,"method":"Runtime.evaluate","params":{"expression": shortcut_js,"returnByValue":True}}))

# Inyección persistente (sobrevive recargas de página, hasta cerrar el perfil)
ws.send(json.dumps({"id":2,"method":"Page.addScriptToEvaluateOnNewDocument","params":{"source": shortcut_js}}))

time.sleep(2)
ws.close()
print("Shortcuts habilitados — presiona F12 en el ginsbrowser para abrir DevTools")
```

---

## Paso 5 — Capturar logs de consola en tiempo real

```python
import websockets.sync.client as wsc, json, time

CDP_PORT = 52101
PAGE_ID  = "86108406DACE8587F0E91B63B60DA3D5"

ws = wsc.connect(f"ws://127.0.0.1:{CDP_PORT}/devtools/page/{PAGE_ID}", max_size=2**23)
ws.send(json.dumps({"id":1,"method":"Runtime.enable"}))
ws.send(json.dumps({"id":2,"method":"Log.enable"}))

print("Escuchando consola... (Ctrl+C para detener)")
while True:
    try:
        ws.socket.settimeout(30)
        msg = json.loads(ws.recv())
        m = msg.get("method","")
        if m == "Runtime.consoleAPICalled":
            p = msg["params"]
            args = " ".join(str(a.get("value", a.get("description","[obj]")))[:200] for a in p.get("args",[]))
            print(f"[{p['type'].upper()}] {args}")
        elif m == "Log.entryAdded":
            e = msg["params"]["entry"]
            print(f"[LOG/{e.get('level','?')}] {e.get('text','')[:200]}")
        elif m == "Runtime.exceptionThrown":
            txt = msg.get("params",{}).get("exceptionDetails",{}).get("text","")
            print(f"[EXCEPTION] {txt}")
    except KeyboardInterrupt:
        break
    except:
        pass
ws.close()
```

---

## Paso 6 — Tomar screenshot del navegador

```python
import websockets.sync.client as wsc, json, base64, time

CDP_PORT = 52101
PAGE_ID  = "86108406DACE8587F0E91B63B60DA3D5"

ws = wsc.connect(f"ws://127.0.0.1:{CDP_PORT}/devtools/page/{PAGE_ID}", max_size=2**23)
ws.send(json.dumps({"id":1,"method":"Page.captureScreenshot","params":{"format":"jpeg","quality":70}}))

deadline = time.time() + 5
while time.time() < deadline:
    try:
        ws.socket.settimeout(1)
        msg = json.loads(ws.recv())
        if msg.get("id") == 1:
            data = msg["result"]["data"]
            open("screenshot.jpg","wb").write(base64.b64decode(data))
            print(f"Guardado: screenshot.jpg ({len(base64.b64decode(data))} bytes)")
            break
    except:
        pass
ws.close()
```

---

## Resumen de puertos

| Servicio | Puerto | Descripción |
|---|---|---|
| `server.py` (FastAPI) | `8585` | API REST de control |
| DICloak UI (CDP) | `9333` | Controla la interfaz de DICloak |
| ginsbrowser perfil | `52101`+ | CDP del navegador de cada perfil (varía) |

---

## Notas importantes

- La inyección de shortcuts (`Page.addScriptToEvaluateOnNewDocument`) **se pierde** al cerrar el perfil.
- El `debug_port` del perfil **cambia** cada vez que se abre — siempre consultar `/profiles/running`.
- La librería correcta es `websockets` (no `websocket-client`): el ginsbrowser rechaza `websocket-client` con error **403 Forbidden**.
- Para tomar screenshot del escritorio completo usar scheduled task + PowerShell `CopyFromScreen`.

```bash
# Instalar dependencia correcta
pip install websockets
```
