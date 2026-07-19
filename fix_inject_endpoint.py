"""Reemplaza el bloque inject-json en cookies_dicloak.py linea por linea."""
PATH = r"C:\Users\NyGsoft\Desktop\publicidad_david\server_dicloak\cookies_dicloak.py"

lines = open(PATH, encoding="utf-8").read().splitlines()

# Encontrar inicio y fin del bloque inject-json
start = end = None
for i, line in enumerate(lines):
    if '@router.post("/inject-json")' in line:
        start = i
    if start and i > start and '@router.get("/")' in line:
        end = i
        break

if start is None or end is None:
    print(f"[ERROR] Bloque no encontrado start={start} end={end}")
    raise SystemExit(1)

print(f"[OK] Bloque encontrado: lineas {start+1} - {end}")

NEW_BLOCK = [
    '    @router.post("/inject-json")',
    '    def inject_json(port: int, payload: dict = Body(...)):',
    '        """',
    '        Recibe un JSON de cache e inyecta cookies + localStorage en el perfil abierto.',
    '        Body: { "cookies": [...], "localStorage": {...} }',
    '        Ejemplo:',
    '          curl -X POST "http://127.0.0.1:8585/cookies/inject-json?port=53706" \\',
    '               -H "Content-Type: application/json" -d @cache.json',
    '        """',
    '        import time as _t, urllib.request as _ur',
    '        import websockets.sync.client as _wsc',
    '',
    '        cookies_list  = payload.get("cookies", [])',
    '        local_storage = payload.get("localStorage", {})',
    '        if not cookies_list:',
    '            return err("El JSON no contiene cookies", 400)',
    '',
    '        def _get_page(p):',
    '            try:',
    '                targets = _json.loads(_ur.urlopen(f"http://127.0.0.1:{p}/json", timeout=5).read())',
    '                page = next((t for t in targets if t.get("type") == "page"',
    '                             and "chatgpt" in t.get("url", "")), None)',
    '                if not page:',
    '                    page = next((t for t in targets if t.get("type") == "page"), None)',
    '                return page["id"] if page else ""',
    '            except Exception:',
    '                return ""',
    '',
    '        page_id = _get_page(port)',
    '        if not page_id:',
    '            return err(f"No hay page target en puerto {port} — perfil abierto?", 404)',
    '',
    '        now = _t.time()',
    '        injected = failed = skipped = 0',
    '',
    '        # 1. Inyectar cookies',
    '        try:',
    '            ws = _wsc.connect(f"ws://127.0.0.1:{port}/devtools/page/{page_id}", max_size=2**23)',
    '            try:',
    '                for i, cookie in enumerate(cookies_list):',
    '                    expires = cookie.get("expires", -1)',
    '                    if expires != -1 and expires < now:',
    '                        skipped += 1',
    '                        continue',
    '                    params = {',
    '                        "name":     cookie["name"],',
    '                        "value":    cookie["value"],',
    '                        "domain":   cookie.get("domain", ""),',
    '                        "path":     cookie.get("path", "/"),',
    '                        "secure":   cookie.get("secure", False),',
    '                        "httpOnly": cookie.get("httpOnly", False),',
    '                        "sameSite": cookie.get("sameSite", "Lax"),',
    '                    }',
    '                    if expires != -1:',
    '                        params["expires"] = expires',
    '                    ws.send(_json.dumps({"id": 100 + i, "method": "Network.setCookie",',
    '                                         "params": params}))',
    '                deadline = _t.time() + 5',
    '                while _t.time() < deadline:',
    '                    try:',
    '                        ws.socket.settimeout(0.3)',
    '                        msg = _json.loads(ws.recv())',
    '                        if msg.get("id", 0) >= 100:',
    '                            if msg.get("result", {}).get("success"):',
    '                                injected += 1',
    '                            else:',
    '                                failed += 1',
    '                    except Exception:',
    '                        break',
    '            finally:',
    '                try: ws.close()',
    '                except Exception: pass',
    '        except Exception as e:',
    '            return err(f"Error inyectando cookies: {e}", 500)',
    '',
    '        # 2. Reconectar y restaurar localStorage',
    '        ls_restored = 0',
    '        if local_storage:',
    '            _t.sleep(2)',
    '            try:',
    '                ws2 = _wsc.connect(',
    '                    f"ws://127.0.0.1:{port}/devtools/page/{_get_page(port) or page_id}",',
    '                    max_size=2**23)',
    '                ls_js = ";".join(',
    '                    f"localStorage.setItem({_json.dumps(k)}, {_json.dumps(v)})"',
    '                    for k, v in local_storage.items()',
    '                )',
    '                ws2.send(_json.dumps({"id": 999, "method": "Runtime.evaluate",',
    '                                       "params": {"expression": ls_js, "returnByValue": True}}))',
    '                _t.sleep(1)',
    '                ws2.close()',
    '                ls_restored = len(local_storage)',
    '            except Exception:',
    '                pass',
    '',
    '        # 3. Navegar a chatgpt.com',
    '        try:',
    '            ws3 = _wsc.connect(',
    '                f"ws://127.0.0.1:{port}/devtools/page/{_get_page(port) or page_id}",',
    '                max_size=2**23)',
    '            ws3.send(_json.dumps({"id": 1, "method": "Page.navigate",',
    '                                   "params": {"url": "https://chatgpt.com"}}))',
    '            _t.sleep(2)',
    '            ws3.close()',
    '        except Exception:',
    '            pass',
    '',
    '        return ok(data={',
    '            "port": port,',
    '            "cookies_injected": injected,',
    '            "cookies_failed": failed,',
    '            "cookies_skipped_expired": skipped,',
    '            "localStorage_keys_restored": ls_restored,',
    '        }, msg=f"Sesion inyectada: {injected} cookies, {ls_restored} localStorage keys")',
    '',
]

# Reemplazar bloque en las lineas
new_lines = lines[:start] + NEW_BLOCK + lines[end:]

# Asegurar que Body este importado
result = "\n".join(new_lines)
result = result.replace(
    "    from fastapi import APIRouter, Request",
    "    from fastapi import APIRouter, Body"
)
if "from fastapi import APIRouter" in result and "Body" not in result.split("from fastapi import APIRouter")[1].split("\n")[0]:
    result = result.replace(
        "    from fastapi import APIRouter",
        "    from fastapi import APIRouter, Body"
    )

open(PATH, "w", encoding="utf-8").write(result)
print(f"[OK] Bloque inject-json reescrito ({len(NEW_BLOCK)} lineas)")
