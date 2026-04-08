"""
ChatGPT Stabilize — Cierra tabs duplicadas y deja una sola lista para generar.

Flujo:
  1. Listar todos los targets CDP del navegador
  2. Filtrar los que son paginas de ChatGPT
  3. Cerrar todas menos la mas reciente
  4. Verificar que la tab restante esta funcional (editor listo)
  5. Si no hay ninguna tab de ChatGPT, crear una nueva
"""
from __future__ import annotations

import json
import time
import urllib.request

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from logger import log_info, log_ok, log_warn, log_error


def _get_browser_ws(port: int) -> str:
    """Obtiene la URL del WebSocket del browser (no page)."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=3) as r:
            data = json.loads(r.read().decode())
            return data.get("webSocketDebuggerUrl", "")
    except Exception:
        return ""


def _list_targets(port: int) -> list[dict]:
    """Lista todos los targets CDP."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=3) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        log_warn(f"No se pudo listar targets en puerto {port}: {e}")
        return []


def stabilize_chatgpt(port: int, timeout: int = 30, keep_target_id: str = "") -> dict:
    """
    Estabiliza la sesion de ChatGPT:
      - Cierra todas las tabs de ChatGPT excepto una
      - Navega la tab restante a https://chatgpt.com/
      - Verifica que el editor este listo

    Args:
        port: Puerto CDP del navegador (ginsbrowser)
        timeout: Tiempo max para esperar que ChatGPT cargue
        keep_target_id: Si se pasa, proteger esta tab y cerrar las demas.
                        Util cuando proxy_bypass ya creo una tab nueva.

    Returns:
        dict con success, tabs_closed, target_ws, message
    """
    try:
        import websockets.sync.client as ws_sync
    except ImportError:
        import subprocess
        subprocess.check_call([sys.executable, "-m", "pip", "install", "websockets", "-q"])
        import websockets.sync.client as ws_sync

    targets = _list_targets(port)
    if not targets:
        return {"success": False, "error": "No se pudo conectar al navegador CDP", "tabs_closed": 0}

    # Filtrar tabs de ChatGPT
    chatgpt_tabs = []
    other_tabs = []
    for t in targets:
        url = (t.get("url") or "").lower()
        if t.get("type") == "page" and "chatgpt.com" in url:
            chatgpt_tabs.append(t)
        elif t.get("type") == "page":
            other_tabs.append(t)

    log_info(f"Targets totales: {len(targets)} | ChatGPT tabs: {len(chatgpt_tabs)} | Otras: {len(other_tabs)}")

    # Conectar al browser WS para poder cerrar tabs
    browser_ws = _get_browser_ws(port)
    if not browser_ws:
        return {"success": False, "error": "No se pudo obtener browser WebSocket", "tabs_closed": 0}

    try:
        ws = ws_sync.connect(browser_ws, max_size=2**22)
    except Exception as e:
        return {"success": False, "error": f"No se pudo conectar al browser WS: {e}", "tabs_closed": 0}

    msg_id = 0

    def cdp(method, params=None):
        nonlocal msg_id
        msg_id += 1
        ws.send(json.dumps({"id": msg_id, "method": method, "params": params or {}}))
        while True:
            resp = json.loads(ws.recv(timeout=10))
            if resp.get("id") == msg_id:
                return resp

    tabs_closed = 0
    survivor_ws = ""

    try:
        if len(chatgpt_tabs) == 0:
            # No hay tab de ChatGPT — crear una
            log_info("No hay tabs de ChatGPT, creando una nueva...")
            r = cdp("Target.createTarget", {"url": "https://chatgpt.com/"})
            if r.get("result"):
                target_id = r["result"]["targetId"]
                log_ok(f"Tab ChatGPT creada: {target_id}")
                # Esperar a que aparezca en /json
                time.sleep(3)
                for t in _list_targets(port):
                    if t.get("id") == target_id:
                        survivor_ws = t.get("webSocketDebuggerUrl", "")
                        break
            else:
                return {"success": False, "error": "No se pudo crear tab de ChatGPT", "tabs_closed": 0}

        elif len(chatgpt_tabs) == 1:
            # Ya hay exactamente una — perfecto
            survivor_ws = chatgpt_tabs[0].get("webSocketDebuggerUrl", "")
            log_info("Ya hay exactamente 1 tab de ChatGPT, no hay que cerrar nada.")

        else:
            # Multiples tabs — determinar cual mantener
            keep = None
            if keep_target_id:
                # Proteger la tab especifica (creada por proxy_bypass)
                for t in chatgpt_tabs:
                    if t.get("id", "") == keep_target_id:
                        keep = t
                        break
                if keep:
                    log_info(f"Protegiendo tab de proxy_bypass: {keep_target_id[:12]}")
                else:
                    log_warn(f"keep_target_id={keep_target_id[:12]} no encontrado, usando ultima tab")

            if not keep:
                # Sin target especifico — preferir una conversación /c/... existente
                convo_tabs = [t for t in chatgpt_tabs if "/c/" in (t.get("url") or "").lower()]
                if convo_tabs:
                    keep = convo_tabs[-1]
                    log_info(f"Manteniendo conversación existente: {keep.get('url', '')[:60]}")
                else:
                    # Fallback: quedarse con la ultima (mas reciente)
                    keep = chatgpt_tabs[-1]

            survivor_ws = keep.get("webSocketDebuggerUrl", "")
            to_close = [t for t in chatgpt_tabs if t.get("id") != keep.get("id")]

            log_info(f"Cerrando {len(to_close)} tabs duplicadas, manteniendo: {keep.get('url', '')[:60]}")

            for tab in to_close:
                tid = tab.get("id", "")
                if tid:
                    try:
                        cdp("Target.closeTarget", {"targetId": tid})
                        tabs_closed += 1
                        log_info(f"Tab cerrada: {tab.get('url', '')[:60]} (id={tid[:12]})")
                    except Exception as e:
                        log_warn(f"No se pudo cerrar tab {tid[:12]}: {e}")

            time.sleep(1)

        # Verificar que la tab superviviente está funcional (sin navegar de nuevo)
        if survivor_ws:
            try:
                page_ws = ws_sync.connect(survivor_ws, max_size=2**22)

                page_msg_id = 0

                def page_cdp(method, params=None):
                    nonlocal page_msg_id
                    page_msg_id += 1
                    page_ws.send(json.dumps({"id": page_msg_id, "method": method, "params": params or {}}))
                    while True:
                        resp = json.loads(page_ws.recv(timeout=10))
                        if resp.get("id") == page_msg_id:
                            return resp

                # Verificar estado actual — solo navegar si la tab NO está en chatgpt.com
                page_msg_id += 1
                page_ws.send(json.dumps({
                    "id": page_msg_id,
                    "method": "Runtime.evaluate",
                    "params": {"expression": "window.location.href", "returnByValue": True}
                }))
                try:
                    resp = json.loads(page_ws.recv(timeout=5))
                    current_url = resp.get("result", {}).get("result", {}).get("value", "")
                except Exception:
                    current_url = ""

                if "chatgpt.com" not in current_url.lower():
                    # Solo navegar si no está en ChatGPT
                    page_cdp("Page.navigate", {"url": "https://chatgpt.com/"})
                    log_info("Tab no estaba en ChatGPT, navegando...")
                    time.sleep(3)
                else:
                    log_info(f"Tab ya en ChatGPT: {current_url[:60]}")

                # Esperar que el editor esté listo
                deadline = time.time() + timeout
                editor_ready = False
                while time.time() < deadline:
                    page_msg_id += 1
                    page_ws.send(json.dumps({
                        "id": page_msg_id,
                        "method": "Runtime.evaluate",
                        "params": {
                            "expression": """(() => {
                                const title = document.title || '';
                                if (title.toLowerCase().includes('un momento') || title.toLowerCase().includes('just a moment'))
                                    return 'CAPTCHA';
                                const editor = document.querySelector('#prompt-textarea[contenteditable="true"]');
                                return (editor && editor.getBoundingClientRect().width > 50) ? 'READY' : 'LOADING';
                            })()""",
                            "returnByValue": True,
                        }
                    }))
                    try:
                        resp = json.loads(page_ws.recv(timeout=5))
                        val = resp.get("result", {}).get("result", {}).get("value", "")
                        if val == "READY":
                            editor_ready = True
                            break
                        elif val == "CAPTCHA":
                            log_info("Esperando CAPTCHA de Cloudflare...")
                    except Exception:
                        pass
                    time.sleep(2)

                page_ws.close()

                if editor_ready:
                    log_ok("ChatGPT estabilizado — editor listo")
                else:
                    log_warn(f"Editor no listo tras {timeout}s, pero la tab existe")

            except Exception as e:
                log_warn(f"Error estabilizando tab superviviente: {e}")

        return {
            "success": True,
            "tabs_closed": tabs_closed,
            "chatgpt_tabs_found": len(chatgpt_tabs),
            "target_ws": survivor_ws,
            "message": f"Estabilizado: {tabs_closed} tabs cerradas, 1 tab activa",
        }

    except Exception as e:
        log_error(f"Error en stabilize_chatgpt: {e}")
        return {"success": False, "error": str(e), "tabs_closed": tabs_closed}
    finally:
        try:
            ws.close()
        except Exception:
            pass
