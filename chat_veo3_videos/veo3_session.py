"""
Veo3 Session — Navega a Google Flow (Veo 3), maneja login y estabiliza.

Python puro + CDP WebSocket. Sin Playwright, sin Node.js.
Cross-platform: Windows, Mac, Linux.
"""
from __future__ import annotations

import json
import time
import urllib.request
from dataclasses import dataclass, field

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from logger import log_info, log_ok, log_warn, log_error
from platform_utils import (
    os_click, os_click_window, find_browser_hwnd,
    get_visible_hwnds, find_new_tooltip_hwnd, get_window_size, IS_WINDOWS,
)


VEO3_URL = "https://labs.google/fx/tools/flow"
VIDEO_FX_URL = "https://labs.google/fx/tools/video-fx"


@dataclass
class Veo3Session:
    """Sesión CDP activa con Google Flow (Veo 3)."""
    port: int
    ws_url: str = ""
    _ws: object = field(default=None, repr=False)
    _msg_id: int = field(default=0, repr=False)

    def connect(self) -> bool:
        """Conecta al CDP del navegador."""
        try:
            import websockets.sync.client as ws_sync
        except ImportError:
            import subprocess
            subprocess.check_call([sys.executable, "-m", "pip", "install", "websockets", "-q"])
            import websockets.sync.client as ws_sync

        # Buscar página de Flow o cualquier página activa
        ws_url = self._find_best_target()
        if not ws_url:
            log_warn(f"No se encontró página en puerto {self.port}")
            return False

        try:
            self._ws = ws_sync.connect(ws_url, max_size=2**22)
            log_ok(f"Veo3 conectado en puerto {self.port}")
            return True
        except Exception as e:
            log_warn(f"Error conectando: {e}")
            self._ws = None
            return False

    def _find_best_target(self) -> str:
        """Busca la mejor página para conectar (Flow > cualquier página)."""
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json", timeout=3) as resp:
                targets = json.loads(resp.read().decode("utf-8"))
        except Exception:
            return ""

        # Prioridad: página de Flow > cualquier página
        flow_target = None
        any_page = None
        for t in targets:
            if t.get("type") != "page":
                continue
            url = (t.get("url") or "").lower()
            if "labs.google/fx" in url and "accounts.google" not in url:
                flow_target = t
                break
            if not any_page and url and url != "about:blank":
                any_page = t

        target = flow_target or any_page
        return target.get("webSocketDebuggerUrl", "") if target else ""

    def is_connected(self) -> bool:
        if self._ws is None:
            return False
        try:
            self._ws.ping()
            return True
        except Exception:
            self._ws = None
            return False

    def _ensure_connected(self) -> bool:
        if self.is_connected():
            return True
        return self.connect()

    def evaluate(self, expression: str, timeout: int = 10, await_promise: bool = False) -> str | None:
        """Evalúa JavaScript en la página."""
        if not self._ensure_connected():
            return None

        self._msg_id += 1
        msg = json.dumps({
            "id": self._msg_id,
            "method": "Runtime.evaluate",
            "params": {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": await_promise,
            }
        })

        try:
            self._ws.send(msg)
            resp_raw = self._ws.recv(timeout=timeout)
            data = json.loads(resp_raw)
            result = data.get("result", {}).get("result", {})
            return result.get("value", json.dumps(result))
        except Exception as e:
            log_warn(f"CDP evaluate error: {e}")
            self._ws = None
            return None

    def navigate(self, url: str) -> bool:
        """Navega a una URL."""
        if not self._ensure_connected():
            return False

        self._msg_id += 1
        msg = json.dumps({
            "id": self._msg_id,
            "method": "Page.navigate",
            "params": {"url": url}
        })

        try:
            self._ws.send(msg)
            self._ws.recv(timeout=10)
            return True
        except Exception as e:
            log_warn(f"Navigate error: {e}")
            return False

    def close(self):
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass
            self._ws = None

    # ── Verificaciones ───────────────────────────────────────────────────

    def check_browser_stable(self) -> dict:
        """Verifica que el navegador esté estable y listo."""
        result = self.evaluate("""(() => {
            return JSON.stringify({
                url: window.location.href,
                title: document.title,
                readyState: document.readyState,
                hasBody: !!document.body,
                bodyLength: (document.body?.innerText || '').length,
            });
        })()""")

        if not result:
            return {"stable": False, "reason": "No se pudo evaluar JS"}

        try:
            info = json.loads(result)
            info["stable"] = info.get("readyState") == "complete" and info.get("hasBody", False)
            return info
        except Exception:
            return {"stable": False, "reason": "Respuesta inválida"}

    def detect_google_login(self) -> bool:
        """Detecta si Google está pidiendo autenticación."""
        url = (self.evaluate("window.location.href") or "").lower()
        return ("accounts.google.com" in url
                or "auth/signin" in url
                or "error=callback" in url
                or "sign in" in (self.evaluate("document.title") or "").lower())

    def handle_google_login(self, timeout_sec: int = 45) -> bool:
        """Maneja el login de Google: click en cuenta guardada."""
        log_info("Login de Google detectado. Seleccionando cuenta guardada...")

        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            url = (self.evaluate("window.location.href") or "").lower()

            # Si ya salió de accounts.google → login completado
            if "labs.google/fx" in url and "accounts.google" not in url:
                log_ok("Login de Google completado")
                return True

            # Primero intentar click en cuenta guardada (account chooser)
            clicked = self.evaluate("""(() => {
                // Estrategia 1: data-identifier (cuenta guardada)
                const byId = document.querySelector('[data-identifier]');
                if (byId) { byId.click(); return 'data-identifier'; }

                // Estrategia 2: primer <li> con email
                const items = document.querySelectorAll('ul li');
                for (const li of items) {
                    const text = li.innerText || '';
                    if (text.includes('@')) { li.click(); return 'email-li'; }
                }

                // Estrategia 3: div con data-email
                const emailDiv = document.querySelector('[data-email]');
                if (emailDiv) { emailDiv.click(); return 'data-email'; }

                // Estrategia 4: cualquier elemento con email visible
                const all = document.querySelectorAll('div, a, button');
                for (const el of all) {
                    const t = (el.innerText || '').trim();
                    if (/@.*\\.com/.test(t) && t.length < 60) { el.click(); return 'email-text'; }
                }

                return null;
            })()""")

            if clicked and clicked != "null":
                log_ok(f"Click en cuenta de Google ({clicked})")
                time.sleep(3)

                # Reconectar — el click navega a otra página y rompe el WebSocket
                self._ws = None
                self.connect()

                # Verificar si necesita contraseña
                url_after = (self.evaluate("window.location.href") or "").lower()
                if "accounts.google" in url_after and "challenge/pwd" in url_after:
                    log_info("Página de contraseña detectada. Usando OS click para autofill DiCloak...")
                    self._handle_password_page()
                    time.sleep(5)

                elif "accounts.google" in url_after:
                    # Otra página de Google — click en Siguiente genérico
                    self.evaluate("""(() => {
                        const btns = Array.from(document.querySelectorAll('button, [role="button"]'));
                        const next = btns.find(b => {
                            const t = (b.innerText || '').toLowerCase();
                            return t.includes('next') || t.includes('siguiente')
                                || t.includes('continuar') || t.includes('sign in')
                                || t.includes('iniciar') || t.includes('acceder');
                        });
                        if (next) next.click();
                    })()""")
                    time.sleep(4)
                    self._ws = None
                    self.connect()
                continue

            # No encontró cuenta — buscar botón "Sign in with Google"
            self.evaluate("""(() => {
                const all = Array.from(document.querySelectorAll('button, a, [role="button"]'));
                const btn = all.find(b => {
                    const t = (b.innerText || '').toLowerCase();
                    return t.includes('sign in with google') || t.includes('iniciar sesión con google')
                        || t.includes('sign in') || t.includes('iniciar sesión')
                        || t.includes('try signing') || t.includes('intentar');
                });
                if (btn) btn.click();
            })()""")
            time.sleep(4)
            self._ws = None
            self.connect()

        # Verificación final — usar misma lógica que is_on_flow()
        final_url = (self.evaluate("window.location.href") or "").lower()
        if self.is_on_flow():
            log_ok("Login completado")
            return True

        # Si llegó a labs.google pero con error, re-navegar
        if "labs.google" in final_url:
            log_info("En labs.google con error, re-navegando a Flow...")
            self.navigate(VEO3_URL)
            time.sleep(5)
            if self.is_on_flow():
                log_ok("Login completado tras re-navegación")
                return True

        log_error("No se pudo completar el login de Google")
        return False

    def _get_screen_coords(self, selector: str) -> dict | None:
        """Obtiene coordenadas absolutas de pantalla de un elemento DOM.
        Necesario para OS clicks que activan el autofill nativo de DiCloak.
        """
        result = self.evaluate(f"""(() => {{
            const el = document.querySelector('{selector}');
            if (!el) return null;
            const rect = el.getBoundingClientRect();
            return JSON.stringify({{
                cx: rect.x + rect.width / 2,
                cy: rect.y + rect.height / 2,
                bottom: rect.y + rect.height,
                screenX: window.screenX,
                screenY: window.screenY,
                chromeH: window.outerHeight - window.innerHeight,
            }});
        }})()""")
        if not result:
            return None
        try:
            data = json.loads(result)
            data["screen_cx"] = int(data["screenX"] + data["cx"])
            data["screen_cy"] = int(data["screenY"] + data["chromeH"] + data["cy"])
            data["screen_bottom"] = int(data["screenY"] + data["chromeH"] + data["bottom"])
            return data
        except Exception:
            return None

    def _handle_password_page(self) -> bool:
        """Maneja la página de contraseña de Google activando el autofill de DiCloak.

        Usa pywinauto para clicks sin mover el cursor del usuario:
        1. Busca la ventana del browser por HWND
        2. Click en campo password → abre tooltip de DiCloak (ventana separada)
        3. Detecta la ventana del tooltip (Chrome_WidgetWin_*)
        4. Click en el centro del tooltip → DiCloak llena la contraseña
        5. Click CDP en Siguiente

        Fallback a pyautogui (mueve cursor) si pywinauto no está disponible.
        """
        # 1. Obtener coordenadas del campo password
        pwd_coords = self._get_screen_coords('input[name="Passwd"]')
        if not pwd_coords:
            pwd_coords = self._get_screen_coords('input[type="password"]')
        if not pwd_coords:
            log_warn("No se encontró campo de contraseña")
            return False

        chrome_h = pwd_coords["chromeH"]
        client_x = int(pwd_coords["cx"])
        client_y = int(chrome_h + pwd_coords["cy"])

        # 2. Buscar ventana del browser
        browser_hwnd = find_browser_hwnd(int(pwd_coords["screenX"]))

        if browser_hwnd:
            # === Ruta pywinauto (sin mover cursor) ===
            log_info("Usando pywinauto para autofill (sin mover cursor)")

            # Snapshot de ventanas antes del click
            hwnds_before = get_visible_hwnds()

            # Click en campo password
            os_click_window(browser_hwnd, client_x, client_y)
            time.sleep(2)

            # Verificar si ya se llenó
            pwd_len = self.evaluate("document.querySelector('input[name=\"Passwd\"]')?.value.length || 0")
            if pwd_len and int(pwd_len) > 0:
                log_ok(f"Password llenado automáticamente ({pwd_len} chars)")
            else:
                # Buscar ventana del tooltip (Chrome_WidgetWin_*) con retry
                tooltip_hwnd = None
                for attempt in range(3):
                    tooltip_hwnd = find_new_tooltip_hwnd(hwnds_before)
                    if tooltip_hwnd:
                        break
                    # Re-click y esperar más
                    time.sleep(1)
                    hwnds_before = get_visible_hwnds()
                    os_click_window(browser_hwnd, client_x, client_y)
                    time.sleep(2)

                if tooltip_hwnd:
                    tw, th = get_window_size(tooltip_hwnd)
                    log_info(f"Tooltip encontrado (hwnd={tooltip_hwnd}, {tw}x{th})")
                    time.sleep(0.5)  # Esperar a que DiCloak renderice el tooltip
                    os_click_window(tooltip_hwnd, tw // 2, th // 2)
                    time.sleep(2)

                    pwd_len = self.evaluate("document.querySelector('input[name=\"Passwd\"]')?.value.length || 0")
                    if pwd_len and int(pwd_len) > 0:
                        log_ok(f"Password autofill DiCloak exitoso ({pwd_len} chars)")
                    else:
                        # Retry: re-click campo, buscar tooltip, click tooltip
                        log_info("Reintentando autofill...")
                        hwnds_before = get_visible_hwnds()
                        os_click_window(browser_hwnd, client_x, client_y)
                        time.sleep(2)
                        tooltip_hwnd = find_new_tooltip_hwnd(hwnds_before)
                        if tooltip_hwnd:
                            tw, th = get_window_size(tooltip_hwnd)
                            time.sleep(0.5)
                            os_click_window(tooltip_hwnd, tw // 2, th // 2)
                            time.sleep(2)
                        pwd_len = self.evaluate("document.querySelector('input[name=\"Passwd\"]')?.value.length || 0")
                        if pwd_len and int(pwd_len) > 0:
                            log_ok(f"Password autofill exitoso en retry ({pwd_len} chars)")
                        else:
                            log_warn("No se pudo activar autofill de DiCloak")
                            return False
                else:
                    log_warn("No se encontró ventana del tooltip de DiCloak")
                    return False
        else:
            # === Fallback pyautogui (mueve cursor brevemente) ===
            log_info("pywinauto no disponible, usando pyautogui (mueve cursor)")
            self._send_raw("Page.bringToFront")
            time.sleep(0.5)

            os_click(pwd_coords["screen_cx"], pwd_coords["screen_cy"])
            time.sleep(2)

            pwd_len = self.evaluate("document.querySelector('input[name=\"Passwd\"]')?.value.length || 0")
            if not pwd_len or int(pwd_len) == 0:
                tooltip_y = pwd_coords["screen_bottom"] + 25
                os_click(pwd_coords["screen_cx"], tooltip_y)
                time.sleep(2)
                pwd_len = self.evaluate("document.querySelector('input[name=\"Passwd\"]')?.value.length || 0")

            if not pwd_len or int(pwd_len) == 0:
                log_warn("No se pudo activar autofill de DiCloak")
                return False
            log_ok(f"Password autofill exitoso ({pwd_len} chars)")

        # 3. Click en Siguiente via CDP
        clicked_next = self.evaluate("""(() => {
            const btns = Array.from(document.querySelectorAll('button, [role="button"]'));
            const next = btns.find(b => {
                const t = (b.innerText || '').toLowerCase();
                return t.includes('next') || t.includes('siguiente') || t.includes('continuar');
            });
            if (next) { next.click(); return next.innerText.trim(); }
            return null;
        })()""")
        if clicked_next:
            log_info(f"Click CDP en '{clicked_next}'")
            time.sleep(4)
            # Reconectar — Siguiente navega a otra página
            self._ws = None
            self.connect()

        return True

    def _send_raw(self, method: str, params: dict | None = None) -> dict | None:
        """Envía comando CDP raw sin wrapper de evaluate."""
        if not self._ensure_connected():
            return None
        self._msg_id += 1
        msg = json.dumps({"id": self._msg_id, "method": method, "params": params or {}})
        try:
            self._ws.send(msg)
            return json.loads(self._ws.recv(timeout=10))
        except Exception:
            return None

    def is_on_flow(self) -> bool:
        """Verifica si está en la página de Flow (Veo 3), no en login/error."""
        url = (self.evaluate("window.location.href") or "").lower()
        if "accounts.google" in url:
            return False
        if "error=callback" in url or "auth/signin" in url:
            return False
        return "labs.google/fx" in url

    def wait_for_flow_ready(self, timeout_sec: int = 30) -> bool:
        """Espera que Flow esté completamente cargado."""
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            ready = self.evaluate("""(() => {
                const url = window.location.href.toLowerCase();
                if (!url.includes('labs.google/fx')) return 'NOT_ON_FLOW';
                if (document.readyState !== 'complete') return 'LOADING';
                // Buscar elementos de Flow cargados
                const hasButtons = document.querySelectorAll('button').length > 3;
                const hasMain = !!document.querySelector('main') || !!document.querySelector('[role="main"]');
                if (hasButtons || hasMain) return 'READY';
                return 'LOADING';
            })()""")

            if ready and "READY" in str(ready):
                return True
            time.sleep(1)

        return False


# ── Función principal ────────────────────────────────────────────────────────

def _cleanup_tabs(port: int) -> str:
    """Cierra todos los tabs excepto uno y retorna el WebSocket del tab que queda."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=3) as resp:
            targets = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return ""

    pages = [t for t in targets if t.get("type") == "page"]
    log_info(f"Tabs abiertos: {len(pages)}. Limpiando...")

    if not pages:
        return ""

    # Mantener solo el primer tab
    keep = pages[0]
    for tab in pages[1:]:
        tab_id = tab.get("id", "")
        if tab_id:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/json/close/{tab_id}", timeout=3)
            except Exception:
                pass

    closed = len(pages) - 1
    if closed > 0:
        log_ok(f"{closed} tab(s) cerrado(s). Queda 1 tab activo.")

    return keep.get("webSocketDebuggerUrl", "")


def navigate_and_stabilize(port: int, timeout: int = 60) -> dict:
    """
    Limpia tabs, navega a Veo 3, maneja login de Google, y retorna estable.

    Args:
        port: Puerto CDP del navegador (de /profiles/open)
        timeout: Timeout total en segundos

    Returns:
        dict con status, url actual, y detalles
    """
    deadline = time.time() + timeout

    # 1. Limpiar tabs duplicados
    ws_url = _cleanup_tabs(port)
    if not ws_url:
        return {"success": False, "error": f"No hay tabs en puerto {port}"}

    time.sleep(1)

    session = Veo3Session(port=port)
    if not session.connect():
        return {"success": False, "error": f"No se pudo conectar en puerto {port}"}

    try:
        # 2. Verificar estabilidad
        log_info("Verificando estabilidad del navegador...")
        for _ in range(10):
            stability = session.check_browser_stable()
            if stability.get("stable"):
                break
            time.sleep(1)

        log_ok("Navegador estable")

        # 3. Navegar directo a Veo 3
        log_info(f"Navegando a {VEO3_URL}...")
        session.navigate(VEO3_URL)
        time.sleep(3)

        # Forzar reconexión — la navegación puede romper el WebSocket
        session._ws = None
        session.connect()

        # Esperar a que la URL se estabilice (la redirección a Google tarda)
        for _ in range(8):
            url_check = (session.evaluate("window.location.href") or "").lower()
            if "accounts.google" in url_check:
                break  # Ya redirigió al login
            if session.is_on_flow():
                # Verificar que realmente cargó (no es pre-redirect)
                ready = session.evaluate("document.readyState")
                has_content = session.evaluate("document.querySelectorAll('button').length > 3")
                if ready == "complete" and has_content:
                    break  # Realmente está en Flow cargado
            time.sleep(1)
            # Reconectar si la redirección rompió el WS
            if not session.is_connected():
                session.connect()

        # 4. Manejar login si aparece (loop hasta que estemos en Flow)
        max_login_attempts = 5
        for attempt in range(max_login_attempts):
            # Reconectar si se perdió la conexión
            if not session.is_connected():
                session.connect()

            url = (session.evaluate("window.location.href") or "").lower()
            log_info(f"Intento {attempt + 1}/{max_login_attempts}: {url[:80]}")

            if session.is_on_flow():
                # Verificar que no es pre-redirect (URL de Flow pero sin contenido real)
                has_buttons = session.evaluate("document.querySelectorAll('button').length > 3")
                if has_buttons:
                    break

            if session.detect_google_login():
                log_info(f"Login detectado (intento {attempt + 1}/{max_login_attempts})")
                remaining = int(deadline - time.time())
                if remaining < 5:
                    break
                session.handle_google_login(timeout_sec=min(30, remaining))
                time.sleep(3)

                # Después del login, navegar a Flow si no redirigió
                if not session.is_on_flow():
                    session.navigate(VEO3_URL)
                    time.sleep(4)
            else:
                # No es login ni Flow — esperar
                time.sleep(2)

        # 5. Esperar que Flow cargue
        if session.is_on_flow():
            remaining = int(deadline - time.time())
            session.wait_for_flow_ready(timeout_sec=max(5, remaining))

        # 6. Estado final
        final_url = session.evaluate("window.location.href") or ""
        final_title = session.evaluate("document.title") or ""

        if session.is_on_flow():
            log_ok(f"Veo 3 estable: {final_title}")
            return {
                "success": True,
                "port": port,
                "url": final_url,
                "title": final_title,
                "stable": True,
            }

        return {
            "success": False,
            "error": "No se pudo llegar a Flow",
            "url": final_url,
            "title": final_title,
        }

    finally:
        session.close()
