"""Agrega el endpoint POST /cookies/inject-json a build_router() en cookies_dicloak.py"""
PATH = r"C:\Users\NyGsoft\Desktop\publicidad_david\server_dicloak\cookies_dicloak.py"

OLD = """    @router.get("/")
    def list_cached():"""

NEW = '''    @router.post("/inject-json")
    def inject_json(port: int, request: Request):
        """
        Recibe un JSON de cache y lo inyecta en el perfil abierto en `port`.
        Body: el contenido de cache/cookies/{Perfil}.json
        Ejemplo: POST /cookies/inject-json?port=53706
        """
        import time as _time
        import urllib.request as _ur

        try:
            body = request.body() if callable(request.body) else b""
        except Exception:
            body = b""

        # Leer body sincrono (FastAPI sync route)
        import threading
        result_holder = {}
        def read_body():
            import asyncio
            async def _read():
                return await request.body()
            loop = asyncio.new_event_loop()
            try:
                result_holder["data"] = loop.run_until_complete(_read())
            finally:
                loop.close()
        t = threading.Thread(target=read_body)
        t.start()
        t.join(timeout=10)
        body = result_holder.get("data", b"")

        if not body:
            return err("Body vacio — enviar JSON de cache en el body", 400)

        try:
            saved = _json.loads(body)
        except Exception as e:
            return err(f"JSON invalido: {e}", 400)

        cookies_list  = saved.get("cookies", [])
        local_storage = saved.get("localStorage", {})

        if not cookies_list:
            return err("El JSON no contiene cookies", 400)

        # Obtener page_id del perfil abierto
        def _get_page(p):
            try:
                targets = _json.loads(_ur.urlopen(f"http://127.0.0.1:{p}/json", timeout=5).read())
                page = next((t for t in targets if t.get("type") == "page"
                             and "chatgpt" in t.get("url", "")), None)
                if not page:
                    page = next((t for t in targets if t.get("type") == "page"), None)
                return page["id"] if page else ""
            except Exception:
                return ""

        page_id = _get_page(port)
        if not page_id:
            return err(f"No se encontro page target en puerto {port} — perfil abierto?", 404)

        # Importar websockets (ya disponible en el proyecto)
        import websockets.sync.client as _wsc

        now = _time.time()
        injected = failed = skipped = 0

        # 1. Inyectar cookies via Network.setCookie
        try:
            ws = _wsc.connect(f"ws://127.0.0.1:{port}/devtools/page/{page_id}", max_size=2**23)
            try:
                for i, cookie in enumerate(cookies_list):
                    expires = cookie.get("expires", -1)
                    if expires != -1 and expires < now:
                        skipped += 1
                        continue
                    params = {
                        "name":     cookie["name"],
                        "value":    cookie["value"],
                        "domain":   cookie.get("domain", ""),
                        "path":     cookie.get("path", "/"),
                        "secure":   cookie.get("secure", False),
                        "httpOnly": cookie.get("httpOnly", False),
                        "sameSite": cookie.get("sameSite", "Lax"),
                    }
                    if expires != -1:
                        params["expires"] = expires
                    ws.send(_json.dumps({"id": 100 + i, "method": "Network.setCookie",
                                         "params": params}))
                # Leer respuestas
                deadline = _time.time() + 5
                while _time.time() < deadline:
                    try:
                        ws.socket.settimeout(0.3)
                        msg = _json.loads(ws.recv())
                        if msg.get("id", 0) >= 100:
                            if msg.get("result", {}).get("success"):
                                injected += 1
                            else:
                                failed += 1
                    except Exception:
                        break
            finally:
                try: ws.close()
                except Exception: pass
        except Exception as e:
            return err(f"Error inyectando cookies: {e}", 500)

        # 2. Reconectar y restaurar localStorage
        ls_restored = 0
        if local_storage:
            _time.sleep(2)
            try:
                page_id2 = _get_page(port) or page_id
                ws2 = _wsc.connect(f"ws://127.0.0.1:{port}/devtools/page/{page_id2}",
                                    max_size=2**23)
                ls_js = ";\\n".join(
                    f"localStorage.setItem({_json.dumps(k)}, {_json.dumps(v)})"
                    for k, v in local_storage.items()
                )
                ws2.send(_json.dumps({"id": 999, "method": "Runtime.evaluate",
                                       "params": {"expression": ls_js, "returnByValue": True}}))
                _time.sleep(1)
                ws2.close()
                ls_restored = len(local_storage)
            except Exception:
                pass

        # 3. Navegar a chatgpt.com para aplicar sesion
        try:
            page_id3 = _get_page(port) or page_id
            ws3 = _wsc.connect(f"ws://127.0.0.1:{port}/devtools/page/{page_id3}", max_size=2**23)
            ws3.send(_json.dumps({"id": 1, "method": "Page.navigate",
                                   "params": {"url": "https://chatgpt.com"}}))
            _time.sleep(2)
            ws3.close()
        except Exception:
            pass

        return ok(data={
            "port": port,
            "cookies_injected": injected,
            "cookies_failed": failed,
            "cookies_skipped_expired": skipped,
            "localStorage_keys_restored": ls_restored,
        }, msg=f"Sesion inyectada: {injected} cookies, {ls_restored} localStorage keys")

    @router.get("/")
    def list_cached():'''

src = open(PATH, encoding="utf-8").read()

# Necesitamos agregar Request al import de FastAPI
if "from fastapi import APIRouter" in src and "Request" not in src.split("from fastapi import")[1].split("\n")[0]:
    src = src.replace(
        "    from fastapi import APIRouter",
        "    from fastapi import APIRouter, Request"
    )
    print("[OK] Request agregado al import de FastAPI")
else:
    print("[OK] Request ya importado o no aplica")

if OLD in src:
    src = src.replace(OLD, NEW, 1)
    open(PATH, "w", encoding="utf-8").write(src)
    print("[OK] Endpoint /inject-json agregado a build_router()")
elif "inject-json" in src:
    print("[OK] Endpoint ya existe")
else:
    print("[ERROR] No se encontro el punto de insercion")
