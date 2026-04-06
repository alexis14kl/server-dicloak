"""
Proxy Bypass — Crea un BrowserContext sin proxy cuando el proxy del perfil está muerto.

DiCloak lanza ginsbrowser con --proxy-server=IP:PORT que no se puede cambiar en runtime.
Cuando ese proxy se cae, ChatGPT muestra ERR_TUNNEL_CONNECTION_FAILED.

Solución: usar CDP Target.createBrowserContext con proxyServer="" (conexión directa)
y navegar ChatGPT en esa nueva tab. El fingerprint del perfil se mantiene.

Uso:
    port = ensure_chatgpt_reachable(port)
    # port ahora tiene ChatGPT funcionando (con o sin proxy bypass)
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
import urllib.request
from typing import Optional

parent = __import__('os').path.dirname(__import__('os').path.dirname(__import__('os').path.abspath(__file__)))
if parent not in sys.path:
    sys.path.insert(0, parent)
from logger import log_info, log_ok, log_warn, log_error


def _get_proxy_from_cmdline(port: int) -> str:
    """Extrae el --proxy-server del proceso ginsbrowser que usa este puerto CDP."""
    try:
        if sys.platform == "win32":
            result = subprocess.run(
                ["wmic", "process", "where", "name='ginsbrowser.exe'", "get", "CommandLine", "/format:list"],
                capture_output=True, text=True, timeout=10,
            )
            for line in result.stdout.split("\n"):
                if f"--remote-debugging-port={port}" in line:
                    m = re.search(r'--proxy-server=(\S+)', line)
                    if m:
                        return m.group(1)
        else:
            result = subprocess.run(["ps", "aux"], capture_output=True, text=True, timeout=5)
            for line in result.stdout.split("\n"):
                if f"--remote-debugging-port={port}" in line:
                    m = re.search(r'--proxy-server=(\S+)', line)
                    if m:
                        return m.group(1)
    except Exception:
        pass
    return ""


def _test_proxy(proxy: str, timeout: int = 8) -> bool:
    """Verifica si un proxy HTTP responde."""
    if not proxy:
        return True  # Sin proxy = conexion directa, OK
    try:
        # proxy format: http://IP:PORT or IP:PORT
        proxy_url = proxy if proxy.startswith("http") else f"http://{proxy}"
        handler = urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})
        opener = urllib.request.build_opener(handler)
        req = urllib.request.Request("https://api.ipify.org?format=json", method="GET")
        with opener.open(req, timeout=timeout) as r:
            data = r.read().decode()
            return "ip" in data
    except Exception:
        return False


def _check_page_error(port: int) -> str:
    """Verifica si la pagina de ChatGPT tiene error de conexion."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=3) as r:
            targets = json.loads(r.read().decode())
        for t in targets:
            if "chatgpt" in (t.get("url") or "").lower() and t.get("type") == "page":
                # Conectar via websockets para evaluar JS
                ws_url = t.get("webSocketDebuggerUrl", "")
                if not ws_url:
                    continue
                try:
                    import websockets.sync.client as ws_sync
                    ws = ws_sync.connect(ws_url, max_size=2**20)
                    msg = json.dumps({
                        "id": 1,
                        "method": "Runtime.evaluate",
                        "params": {
                            "expression": """
                                (() => {
                                    const body = (document.body?.innerText || '').substring(0, 500);
                                    if (body.includes('ERR_TUNNEL')) return 'ERR_TUNNEL_CONNECTION_FAILED';
                                    if (body.includes('ERR_PROXY')) return 'ERR_PROXY_CONNECTION_FAILED';
                                    if (body.includes('ERR_CONNECTION')) return 'ERR_CONNECTION';
                                    if (body.includes('no se puede acceder') || body.includes('This site')) return 'PAGE_ERROR';
                                    return 'OK';
                                })()
                            """,
                            "returnByValue": True,
                        }
                    })
                    ws.send(msg)
                    resp = json.loads(ws.recv(timeout=5))
                    ws.close()
                    return resp.get("result", {}).get("result", {}).get("value", "UNKNOWN")
                except Exception:
                    pass
    except Exception:
        pass
    return "UNKNOWN"


def _get_browser_ws(port: int) -> str:
    """Obtiene la URL del WebSocket del browser (no del page)."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=3) as r:
            data = json.loads(r.read().decode())
            return data.get("webSocketDebuggerUrl", "")
    except Exception:
        return ""


def _get_ws_url_for_target(port: int, target_id: str, retries: int = 5) -> str:
    """Busca el webSocketDebuggerUrl de un targetId en /json."""
    for _ in range(retries):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=3) as r:
                targets = json.loads(r.read().decode())
            for t in targets:
                if t.get("id") == target_id:
                    return t.get("webSocketDebuggerUrl", "")
        except Exception:
            pass
        time.sleep(1)
    return ""


def create_direct_chatgpt_tab(port: int, timeout: int = 15) -> Optional[str]:
    """
    Crea una nueva tab con BrowserContext sin proxy y navega a ChatGPT.

    Retorna el targetId de la nueva tab, o None si falla.
    El caller debe buscar esta tab en /json para obtener su WS URL.
    """
    try:
        import websockets.sync.client as ws_sync
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "websockets", "-q"])
        import websockets.sync.client as ws_sync

    browser_ws = _get_browser_ws(port)
    if not browser_ws:
        log_error(f"No se pudo obtener browser WS en puerto {port}")
        return None

    try:
        ws = ws_sync.connect(browser_ws, max_size=2**22,
                             additional_headers={"Origin": ""})
    except Exception:
        # Intentar sin origin header
        try:
            ws = ws_sync.connect(browser_ws, max_size=2**22)
        except Exception as e:
            log_error(f"No se pudo conectar al browser WS: {e}")
            return None

    msg_id = 0

    def cdp(method, params=None):
        nonlocal msg_id
        msg_id += 1
        ws.send(json.dumps({"id": msg_id, "method": method, "params": params or {}}))
        while True:
            resp = json.loads(ws.recv(timeout=30))
            if resp.get("id") == msg_id:
                return resp

    try:
        # Crear tab en el contexto default (comparte sesion/cookies de ChatGPT)
        # No usar createBrowserContext — eso crea ventana aislada sin cookies
        r = cdp("Target.createTarget", {
            "url": "https://chatgpt.com/",
        })
        if not r.get("result"):
            log_error(f"createTarget fallo: {r.get('error', {})}")
            return None

        target_id = r["result"]["targetId"]
        log_ok(f"Tab ChatGPT creada en contexto default: {target_id}")

        # 3. Esperar a que cargue
        time.sleep(timeout)

        return target_id

    except Exception as e:
        log_error(f"Error creando tab sin proxy: {e}")
        return None
    finally:
        try:
            ws.close()
        except Exception:
            pass


def ensure_chatgpt_reachable(port: int) -> tuple[int, str]:
    """
    Verifica que ChatGPT sea accesible en este puerto CDP.
    Si el proxy del perfil esta muerto, crea una tab sin proxy.

    Retorna (port, ws_url):
      - port: el mismo puerto CDP (nueva tab esta en el mismo browser)
      - ws_url: WebSocket URL de la tab correcta ("" si proxy OK y no se creó tab)
    Si falla, retorna (0, "").
    """
    # 1. Verificar si hay proxy y si funciona
    proxy = _get_proxy_from_cmdline(port)
    if proxy:
        log_info(f"Proxy del perfil: {proxy}")
        if _test_proxy(proxy, timeout=8):
            log_ok(f"Proxy vivo — conexion OK")
            return port, ""
        else:
            log_warn(f"Proxy MUERTO: {proxy} — creando tab sin proxy...")
    else:
        log_info("Perfil sin proxy — conexion directa")
        return port, ""

    # 2. Proxy muerto: crear tab nueva sin proxy (no confiar en tab existente)
    target_id = create_direct_chatgpt_tab(port, timeout=15)
    if target_id:
        ws_url = _get_ws_url_for_target(port, target_id)
        if ws_url:
            log_ok(f"Tab sin proxy lista — ws: ...{ws_url[-40:]}")
        else:
            log_warn("Tab creada pero no se encontró su WebSocket URL")
        return port, ws_url

    log_error("No se pudo crear tab sin proxy")
    return 0, ""
