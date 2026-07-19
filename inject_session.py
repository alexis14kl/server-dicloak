"""Inyecta la sesion de #1 Chat Gpt Pro en TEST_COOKIE1 con reconexion WS."""
import sys, json, time, urllib.request
sys.path.insert(0, r"C:\Users\NyGsoft\Desktop\publicidad_david\server_dicloak")
import websockets.sync.client as wsc

SOURCE_PROFILE = "#1 Chat Gpt Pro"
TARGET_PORT    = 53706
CACHE_DIR      = r"C:\Users\NyGsoft\Desktop\publicidad_david\server_dicloak\cache\cookies"

# 1. Cargar cache del perfil fuente
cache_file = CACHE_DIR + "\\" + SOURCE_PROFILE.replace(" ", "_") + ".json"
saved = json.load(open(cache_file, encoding="utf-8"))
cookies      = saved.get("cookies", [])
local_storage = saved.get("localStorage", {})
print(f"[1] Cache cargado: {len(cookies)} cookies, {len(local_storage)} localStorage keys")

# 2. Obtener page_id del perfil destino
def get_page_id(port):
    targets = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=5).read())
    page = next((t for t in targets if t.get("type") == "page" and "chatgpt" in t.get("url", "")), None)
    if not page:
        page = next((t for t in targets if t.get("type") == "page"), None)
    return page["id"] if page else ""

page_id = get_page_id(TARGET_PORT)
print(f"[2] Page ID destino: {page_id[:20]}...")

# 3. Inyectar cookies via Network.setCookie
print(f"[3] Inyectando {len(cookies)} cookies...")
ws = wsc.connect(f"ws://127.0.0.1:{TARGET_PORT}/devtools/page/{page_id}", max_size=2**23)
now = time.time()
injected = failed = skipped = 0
try:
    for i, cookie in enumerate(cookies):
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
        ws.send(json.dumps({"id": 100 + i, "method": "Network.setCookie", "params": params}))

    # Leer respuestas
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            ws.socket.settimeout(0.3)
            msg = json.loads(ws.recv())
            if msg.get("id", 0) >= 100:
                if msg.get("result", {}).get("success"):
                    injected += 1
                else:
                    failed += 1
        except Exception:
            break
except Exception as e:
    print(f"    WS cerrado durante cookies: {e}")
finally:
    try: ws.close()
    except: pass

print(f"    Inyectadas: {injected} | Fallidas: {failed} | Expiradas: {skipped}")

# 4. Reconectar y restaurar localStorage
if local_storage:
    print(f"[4] Restaurando {len(local_storage)} localStorage keys (reconectando)...")
    time.sleep(2)  # esperar posible reload tras cookies
    try:
        page_id2 = get_page_id(TARGET_PORT) or page_id
        ws2 = wsc.connect(f"ws://127.0.0.1:{TARGET_PORT}/devtools/page/{page_id2}", max_size=2**23)
        ls_js = ";\n".join(
            f"localStorage.setItem({json.dumps(k)}, {json.dumps(v)})"
            for k, v in local_storage.items()
        )
        ws2.send(json.dumps({"id": 999, "method": "Runtime.evaluate",
                             "params": {"expression": ls_js, "returnByValue": True}}))
        time.sleep(1)
        ws2.close()
        print(f"    localStorage OK")
    except Exception as e:
        print(f"    localStorage error (no critico): {e}")

# 5. Navegar a ChatGPT para aplicar sesion
print(f"[5] Navegando a chatgpt.com...")
time.sleep(1)
try:
    page_id3 = get_page_id(TARGET_PORT) or page_id
    ws3 = wsc.connect(f"ws://127.0.0.1:{TARGET_PORT}/devtools/page/{page_id3}", max_size=2**23)
    ws3.send(json.dumps({"id": 1, "method": "Page.navigate", "params": {"url": "https://chatgpt.com"}}))
    time.sleep(3)
    ws3.close()
    print("    Navegado OK")
except Exception as e:
    print(f"    Error al navegar: {e}")

print("\n[DONE] Sesion de ChatGPT inyectada en TEST_COOKIE1. Verificar en el navegador.")
