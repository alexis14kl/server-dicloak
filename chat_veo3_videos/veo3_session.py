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

                # Esperar a que la URL cambie — NO reconectar, el WS sigue vivo
                url_after = ""
                for wait in range(10):
                    time.sleep(1)
                    url_after = (self.evaluate("window.location.href") or "").lower()
                    if url_after and url_after != url:
                        log_info(f"URL cambió: {url_after[:60]}")
                        break
                    # Si evaluate falla, reconectar
                    if not url_after:
                        self._ws = None
                        self.connect()

                # Verificar si necesita contraseña
                if "challenge/pwd" in url_after:
                    log_info("Página de contraseña detectada. Usando OS click para autofill DiCloak...")
                    self._handle_password_page()

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
        """Activa autofill de DiCloak en página de contraseña de Google.
        Click real (pyautogui) en campo password → tooltip → autofill → Siguiente.
        """
        # Esperar carga de la página
        for _ in range(10):
            has_pwd = self.evaluate("document.readyState === 'complete' && !!document.querySelector('input[name=\"Passwd\"]')")
            if has_pwd:
                break
            time.sleep(1)
        time.sleep(1)

        # Coordenadas del campo
        pwd_coords = self._get_screen_coords('input[name="Passwd"]')
        if not pwd_coords:
            pwd_coords = self._get_screen_coords('input[type="password"]')
        if not pwd_coords:
            log_warn("No se encontró campo de contraseña")
            return False

        # Click real en campo password
        self._send_raw("Page.bringToFront")
        time.sleep(0.3)
        os_click(pwd_coords["screen_cx"], pwd_coords["screen_cy"])
        time.sleep(2)

        # Click en tooltip DiCloak (debajo del campo)
        pwd_len = self.evaluate("document.querySelector('input[name=\"Passwd\"]')?.value.length || 0")
        if not pwd_len or int(pwd_len) == 0:
            os_click(pwd_coords["screen_cx"], pwd_coords["screen_bottom"] + 25)
            time.sleep(2)
            pwd_len = self.evaluate("document.querySelector('input[name=\"Passwd\"]')?.value.length || 0")

        if not pwd_len or int(pwd_len) == 0:
            log_warn("No se pudo activar autofill de DiCloak")
            return False

        log_ok(f"Password autofill ({pwd_len} chars)")

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
            # Esperar a que navegue
            time.sleep(5)

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
            if not self.is_connected():
                self.connect()

            ready = self.evaluate("""(() => {
                const url = window.location.href.toLowerCase();
                if (!url.includes('labs.google/fx')) return 'NOT_ON_FLOW';
                if (document.readyState !== 'complete') return 'LOADING';
                const hasButtons = document.querySelectorAll('button').length > 3;
                const hasMain = !!document.querySelector('main') || !!document.querySelector('[role="main"]');
                if (hasButtons || hasMain) return 'READY';
                return 'LOADING';
            })()""")

            if ready and "READY" in str(ready):
                return True
            if ready and "NOT_ON_FLOW" in str(ready):
                return False  # Salió de Flow
            time.sleep(2)

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


def open_new_project(port: int, prompt: str = "") -> dict:
    """Abre un nuevo proyecto (chat) en Flow y opcionalmente envía un prompt."""
    session = Veo3Session(port=port)
    if not session.connect():
        return {"success": False, "error": f"No se pudo conectar en puerto {port}"}

    try:
        # Verificar que estamos en Flow
        if not session.is_on_flow():
            url = session.evaluate("window.location.href") or ""
            return {"success": False, "error": "No está en Flow", "url": url}

        # Click en "New project"
        result = session.evaluate("""(() => {
            const btns = Array.from(document.querySelectorAll('button'));
            const newBtn = btns.find(b => {
                const text = (b.innerText || '').toLowerCase();
                return text.includes('new project') || text.includes('nuevo proyecto');
            });
            if (newBtn) {
                newBtn.click();
                return 'CLICKED';
            }
            return 'NOT_FOUND';
        })()""")

        if result != "CLICKED":
            return {"success": False, "error": "No se encontró botón 'New project'"}

        log_ok("Click en 'New project'")

        # Esperar a que la URL cambie a /project/{id}
        project_url = ""
        for _ in range(10):
            time.sleep(1)
            url = session.evaluate("window.location.href") or ""
            if "/project/" in url:
                project_url = url
                break

        title = session.evaluate("document.title") or ""

        # Si hay prompt, pegarlo y enviarlo
        prompt_sent = False
        if prompt:
            prompt_sent = _paste_and_send_prompt(session, prompt)

        return {
            "success": True,
            "port": port,
            "url": project_url or url,
            "title": title,
            "prompt_sent": prompt_sent,
        }

    finally:
        session.close()


def _paste_and_send_prompt(session: Veo3Session, prompt: str) -> bool:
    """Pega un prompt en el chat de Flow y lo envía.
    Replica el método probado de ChatGPT: focus con Selection/Range,
    clear con Backspace real, insert con Input.insertText por chunks.
    """
    # Esperar a que el editor esté listo
    for _ in range(10):
        has_editor = session.evaluate("!!document.querySelector('[contenteditable=\"true\"]')")
        if has_editor:
            break
        time.sleep(1)

    # 1. Focus con Selection/Range (como ChatGPT)
    session.evaluate("""(() => {
        const editor = document.querySelector('[contenteditable="true"]');
        if (!editor) return;
        editor.focus();
        const selection = window.getSelection();
        const range = document.createRange();
        range.selectNodeContents(editor);
        range.collapse(false);
        selection.removeAllRanges();
        selection.addRange(range);
    })()""")
    time.sleep(0.3)

    # 2. Clear con selectNodeContents + Backspace real via CDP
    session.evaluate("""(() => {
        const editor = document.querySelector('[contenteditable="true"]');
        editor.focus();
        const selection = window.getSelection();
        const range = document.createRange();
        range.selectNodeContents(editor);
        selection.removeAllRanges();
        selection.addRange(range);
    })()""")
    session._send_raw("Input.dispatchKeyEvent", {
        "type": "keyDown", "key": "Backspace", "code": "Backspace",
        "windowsVirtualKeyCode": 8, "nativeVirtualKeyCode": 8,
    })
    session._send_raw("Input.dispatchKeyEvent", {
        "type": "keyUp", "key": "Backspace", "code": "Backspace",
        "windowsVirtualKeyCode": 8, "nativeVirtualKeyCode": 8,
    })
    time.sleep(0.3)

    # 3. Focus de nuevo
    session.evaluate("""(() => {
        const editor = document.querySelector('[contenteditable="true"]');
        editor.focus();
        const selection = window.getSelection();
        const range = document.createRange();
        range.selectNodeContents(editor);
        range.collapse(false);
        selection.removeAllRanges();
        selection.addRange(range);
    })()""")
    time.sleep(0.3)

    # 4. Insert con Input.insertText por chunks (método de ChatGPT)
    CHUNK_SIZE = 200
    for i in range(0, len(prompt), CHUNK_SIZE):
        chunk = prompt[i:i + CHUNK_SIZE]
        session._send_raw("Input.insertText", {"text": chunk})
        if i + CHUNK_SIZE < len(prompt):
            time.sleep(0.05)

    time.sleep(1)

    # Verificar que el prompt se registró
    content = session.evaluate("document.querySelector('[contenteditable=\"true\"]')?.innerText || ''")
    if not content or len(content.strip()) < 5:
        log_warn(f"Prompt no se registró en el editor: '{content}'")
        return False

    log_ok(f"Prompt pegado ({len(content.strip())} chars)")

    # 5. Click en botón "Create" (arrow_forward) via CDP
    sent = session.evaluate("""(() => {
        const btns = Array.from(document.querySelectorAll('button'));
        const createBtn = btns.find(b => (b.innerText || '').includes('arrow_forward'));
        if (createBtn && !createBtn.disabled) {
            createBtn.click();
            return 'SENT';
        }
        return 'NO_BUTTON';
    })()""")

    if sent and "SENT" in str(sent):
        log_ok("Prompt enviado")
        return True

    log_warn(f"No se pudo enviar el prompt: {sent}")
    return False


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
        time.sleep(2)

        # Esperar a que la página se resuelva: Flow cargado O redirect a Google login
        # No fiarse solo de la URL — verificar contenido real
        flow_ready = False
        for _ in range(15):
            if not session.is_connected():
                session._ws = None
                session.connect()

            state = session.evaluate("""(() => {
                const url = window.location.href.toLowerCase();
                if (url.includes('accounts.google')) return 'GOOGLE_LOGIN';
                if (url.includes('labs.google/fx')) {
                    const btns = document.querySelectorAll('button').length;
                    if (btns > 3) return 'FLOW_READY';
                    return 'FLOW_LOADING';
                }
                return 'OTHER:' + url.substring(0, 60);
            })()""")

            if state and "GOOGLE_LOGIN" in str(state):
                log_info("Redirigido a Google login")
                break
            if state and "FLOW_READY" in str(state):
                log_ok("Flow cargado directamente (sin login)")
                flow_ready = True
                break
            time.sleep(1)

        # 4. Manejar login si aparece (loop hasta que estemos en Flow)
        max_login_attempts = 5
        for attempt in range(max_login_attempts):
            if flow_ready:
                break

            if not session.is_connected():
                session._ws = None
                session.connect()

            url = (session.evaluate("window.location.href") or "").lower()
            log_info(f"Intento {attempt + 1}/{max_login_attempts}: {url[:80]}")

            # Verificar Flow con contenido real (no solo URL)
            if session.is_on_flow():
                has_content = session.evaluate("document.querySelectorAll('button').length > 3")
                if has_content:
                    log_ok("En Flow con contenido cargado")
                    flow_ready = True
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
        if not session.is_connected():
            session.connect()
        if session.is_on_flow():
            remaining = int(deadline - time.time())
            session.wait_for_flow_ready(timeout_sec=max(10, remaining))

        # 6. Estado final
        if not session.is_connected():
            session.connect()
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
