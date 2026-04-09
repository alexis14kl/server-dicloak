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
from logger import log_info, log_ok, log_warn, log_error, capture_logs
from platform_utils import (
    os_click, os_click_window, find_browser_hwnd,
    get_visible_hwnds, find_new_tooltip_hwnd, get_window_size, IS_WINDOWS,
)


VEO3_URL = "https://labs.google/fx/tools/flow"
VIDEO_FX_URL = "https://labs.google/fx/tools/video-fx"


# ── Errores clasificados del flujo de login ────────────────────────────────
# Códigos expuestos (para orchestrator Django / cliente Python):
#   - password_input_not_found   → No se encontró el campo input[name="Passwd"]
#   - password_not_saved         → El autofill no disparó (Chrome no tiene password guardada para el email)
#   - next_button_not_found      → No se encontró el botón "Siguiente/Next" tras autofill
#   - next_button_click_failed   → Botón encontrado pero CDP dispatchMouseEvent falló
#   - google_flow_redirect_timeout → Tras click en Siguiente, Google no redirigió a labs.google
#   - page_load_timeout          → Tras navegar a Flow, la página no alcanzó readyState=complete
#   - account_chooser_no_accounts → Pantalla "Elige una cuenta" pero no hay [data-identifier]
#   - flow_signin_button_not_found → Landing de Flow con prompt de Sign in pero no se encontró el botón
#   - login_state_unknown        → Página cargó pero no es Flow, ni chooser, ni sign-in, ni password
class Veo3LoginError(RuntimeError):
    """Error clasificado del flujo de login Google → Veo 3."""
    def __init__(self, code: str, message: str = "") -> None:
        self.code = code
        super().__init__(f"[{code}] {message}" if message else f"[{code}]")


@dataclass
class Veo3Session:
    """Sesión CDP activa con Google Flow (Veo 3)."""
    port: int
    ws_url: str = ""
    _ws: object = field(default=None, repr=False)
    _msg_id: int = field(default=0, repr=False)

    def connect(self, cleanup_other_tabs: bool = True) -> bool:
        """Conecta al CDP del navegador.

        Selecciona el tab correcto (Flow > accounts.google > crea uno nuevo),
        opcionalmente cierra los otros tabs irrelevantes para evitar clutter.
        """
        try:
            import websockets.sync.client as ws_sync
        except ImportError:
            import subprocess
            subprocess.check_call([sys.executable, "-m", "pip", "install", "websockets", "-q"])
            import websockets.sync.client as ws_sync

        selected = self._select_or_create_flow_tab()
        if not selected:
            log_warn(f"No se encontró ni pudo crear tab en puerto {self.port}")
            return False

        ws_url = selected.get("webSocketDebuggerUrl", "")
        target_id = selected.get("id", "")
        if not ws_url:
            log_warn(f"Target sin webSocketDebuggerUrl en puerto {self.port}")
            return False

        try:
            self._ws = ws_sync.connect(ws_url, max_size=2**22)
            self.ws_url = ws_url
            log_ok(f"Veo3 conectado en puerto {self.port} — target={target_id[:12]} url={(selected.get('url') or '')[:60]}")
        except Exception as e:
            log_warn(f"Error conectando: {e}")
            self._ws = None
            return False

        if cleanup_other_tabs and target_id:
            self._close_other_tabs(target_id)

        return True

    def _list_page_targets(self) -> list[dict]:
        """Lee /json del CDP y retorna solo los targets de tipo 'page'.

        Timeout duro de 5s. Retorna lista vacía si falla.
        """
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json", timeout=5) as resp:
                raw = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            log_warn(f"No se pudo leer /json en puerto {self.port}: {exc}")
            return []
        if not isinstance(raw, list):
            return []
        return [t for t in raw if isinstance(t, dict) and t.get("type") == "page"]

    def _select_or_create_flow_tab(self) -> dict | None:
        """Elige el tab correcto del perfil DiCloak.

        Prioridad:
          1. Tab con url que contenga 'labs.google/fx' (sesión Flow activa).
          2. Tab con url que contenga 'accounts.google.com' (login en curso).
          3. Tab no-devtools cualquiera (reutiliza para navegar).
          4. Si no hay ninguno → crea uno con Target.createTarget(VEO3_URL).

        Retorna el dict del target seleccionado, o None si todo falló.
        """
        pages = self._list_page_targets()

        flow_target = None
        auth_target = None
        any_page = None
        for t in pages:
            url = (t.get("url") or "").lower()
            # Descartar DevTools embebido
            if url.startswith("devtools://") or "chrome-devtools" in url:
                continue
            if "labs.google/fx" in url and "accounts.google" not in url:
                flow_target = t
                break
            if "accounts.google.com" in url and not auth_target:
                auth_target = t
                continue
            if not any_page:
                any_page = t

        selected = flow_target or auth_target or any_page
        if selected:
            return selected

        # Fallback: crear un tab nuevo navegando a Flow
        log_info(f"Sin tabs utilizables — creando tab nuevo con VEO3_URL")
        new_id = self._create_target(VEO3_URL)
        if not new_id:
            return None
        # Releer /json para obtener el webSocketDebuggerUrl del tab recién creado
        for t in self._list_page_targets():
            if t.get("id") == new_id:
                return t
        return None

    def _create_target(self, url: str) -> str:
        """Crea un tab nuevo via HTTP /json/new?<url>. Retorna targetId o ''.

        DiCloak expone el endpoint clásico de Chromium. Timeout 5s.
        """
        try:
            from urllib.parse import quote
            req_url = f"http://127.0.0.1:{self.port}/json/new?{quote(url, safe=':/?&=')}"
            req = urllib.request.Request(req_url, method="PUT")
            try:
                with urllib.request.urlopen(req, timeout=5) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
            except Exception:
                # Algunos Chromium aceptan GET en lugar de PUT
                with urllib.request.urlopen(req_url, timeout=5) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
            if isinstance(data, dict):
                return data.get("id", "") or ""
        except Exception as exc:
            log_warn(f"createTarget falló: {exc}")
        return ""

    def _close_other_tabs(self, keep_target_id: str) -> int:
        """Cierra todos los tabs 'page' excepto el seleccionado y DevTools.

        Retorna la cantidad de tabs cerrados. Timeout 5s por tab.
        """
        if not keep_target_id:
            return 0
        closed = 0
        for t in self._list_page_targets():
            tab_id = t.get("id", "")
            if not tab_id or tab_id == keep_target_id:
                continue
            url = (t.get("url") or "").lower()
            if url.startswith("devtools://") or "chrome-devtools" in url:
                continue
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{self.port}/json/close/{tab_id}", timeout=5
                ):
                    pass
                closed += 1
            except Exception as exc:
                log_warn(f"closeTarget {tab_id[:12]} falló: {exc}")
        if closed:
            log_ok(f"Cerrados {closed} tab(s) irrelevante(s). Queda 1 activo.")
        return closed

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
        # Reconnect sin cerrar tabs — el cleanup solo corre en el primer
        # connect() de la sesión para no cerrar tabs que alguien más abrió
        # después (p.ej. popups de Google auth legítimos).
        return self.connect(cleanup_other_tabs=False)

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

    # ── Espera de carga y detección de estado post-load ─────────────────────

    def wait_for_page_load(self, timeout_sec: int = 20) -> bool:
        """Espera a que document.readyState === 'complete'.

        Poll cada 0.5s con timeout duro. Sin reconexiones defensivas, sin
        fallbacks. Retorna True si cargó, False si timeout.
        KISS: solo readyState, no esperamos networkidle ni "settle" del JS —
        eso lo cubre el pequeño sleep final.
        """
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            ready = self.evaluate("document.readyState")
            if ready == "complete":
                # Settle mínimo para que el JS monte el DOM (account chooser
                # suele aparecer ~500ms después de ready).
                time.sleep(1.5)
                return True
            time.sleep(0.5)
        return False

    def detect_page_state(self) -> dict:
        """Clasifica el estado de la página actual tras wait_for_page_load.

        Estados posibles (mutuamente excluyentes, devuelto en campo 'state'):
          - 'flow_signin_prompt' → labs.google/fx landing con botón
                                "Sign in with Google" visible (sesión NO activa,
                                requiere click previo antes del chooser).
          - 'flow'            → ya en labs.google/fx (sesión activa)
          - 'password'        → input[name="Passwd"] visible (challenge/pwd)
          - 'account_chooser' → [data-identifier] visible (Elige una cuenta,
                                incluye estado "Saliste de la cuenta")
          - 'sign_in'         → input[type="email"] visible (login completo)
          - 'unknown'         → ninguno de los anteriores

        Retorna dict con: state, url, identifier_count, title.
        Single evaluate() — no hace multiple round-trips.
        """
        raw = self.evaluate("""(() => {
            const url = (window.location.href || '').toLowerCase();
            const title = document.title || '';
            let state = 'unknown';

            // Detecta botón "Sign in with Google" en landing de Flow.
            // Criterios: elemento clickable visible cuyo innerText contenga
            // 'google' Y alguno de {sign in, iniciar sesión, acceder}.
            const hasFlowSigninButton = () => {
                const cands = Array.from(document.querySelectorAll(
                    'button, a, [role="button"]'
                )).filter(el => el.offsetParent !== null);
                return cands.some(el => {
                    const t = ((el.innerText || '') + ' ' +
                               (el.getAttribute('aria-label') || '')).toLowerCase();
                    if (!t.includes('google')) return false;
                    return t.includes('sign in') || t.includes('iniciar sesión')
                        || t.includes('iniciar sesion') || t.includes('acceder');
                });
            };

            const onFlowUrl = url.includes('labs.google/fx') && !url.includes('accounts.google');

            if (onFlowUrl && hasFlowSigninButton()) {
                // Landing de Flow: URL válida pero sesión NO activa — hay que
                // clickear "Sign in with Google" primero.
                state = 'flow_signin_prompt';
            } else if (onFlowUrl) {
                state = 'flow';
            } else if (document.querySelector('input[name="Passwd"]')) {
                state = 'password';
            } else {
                // Account chooser: cualquier [data-identifier] visible (incluso
                // si dice "Saliste de la cuenta" — Google conserva el elemento).
                const idents = Array.from(document.querySelectorAll('[data-identifier]'))
                    .filter(el => el.offsetParent !== null);
                if (idents.length > 0) {
                    state = 'account_chooser';
                } else if (document.querySelector('input[type="email"]')) {
                    state = 'sign_in';
                }
            }

            return JSON.stringify({
                state,
                url,
                title,
                identifier_count: document.querySelectorAll('[data-identifier]').length,
            });
        })()""")

        if not raw:
            return {"state": "unknown", "url": "", "title": "", "identifier_count": 0}
        try:
            return json.loads(raw)
        except Exception:
            return {"state": "unknown", "url": "", "title": "", "identifier_count": 0}

    def click_account_chooser(self) -> str:
        """Click en el primer [data-identifier] visible. Retorna el email o ''.

        Funciona incluso para cuentas en estado "Saliste de la cuenta": Google
        mantiene el elemento y acepta el click, tras lo cual redirige a
        challenge/pwd (flujo ya manejado por _handle_password_page).
        """
        raw = self.evaluate("""(() => {
            const extractEmail = (text) => {
                const m = (text || '').match(/[\\w.+-]+@[\\w-]+\\.[\\w.-]+/);
                return m ? m[0] : '';
            };
            const idents = Array.from(document.querySelectorAll('[data-identifier]'))
                .filter(el => el.offsetParent !== null);
            if (!idents.length) return '';
            const el = idents[0];
            const email = el.getAttribute('data-identifier')
                || extractEmail(el.innerText || '');
            el.click();
            return email || '@unknown';
        })()""")
        return (raw or "").strip()

    def click_flow_signin_button(self) -> bool:
        """Click en el botón "Sign in with Google" de la landing de Flow.

        Invocado cuando detect_page_state devuelve 'flow_signin_prompt'. Busca
        el botón por texto visible + presencia de 'google', lo clickea y deja
        a Google manejar la redirección a accounts.google.com (donde el flujo
        continuará como account_chooser o password).

        Lanza Veo3LoginError('flow_signin_button_not_found') si no aparece.
        KISS: single evaluate, sin polling — el caller ya esperó wait_for_page_load.
        """
        raw = self.evaluate("""(() => {
            const cands = Array.from(document.querySelectorAll(
                'button, a, [role="button"]'
            )).filter(el => el.offsetParent !== null);
            const btn = cands.find(el => {
                const t = ((el.innerText || '') + ' ' +
                           (el.getAttribute('aria-label') || '')).toLowerCase();
                if (!t.includes('google')) return false;
                return t.includes('sign in') || t.includes('iniciar sesión')
                    || t.includes('iniciar sesion') || t.includes('acceder');
            });
            if (!btn) return JSON.stringify({ok: false});
            btn.click();
            return JSON.stringify({
                ok: true,
                text: (btn.innerText || '').trim().slice(0, 60),
            });
        })()""")

        try:
            data = json.loads(raw or "{}")
        except Exception:
            data = {}

        if not data.get("ok"):
            raise Veo3LoginError(
                "flow_signin_button_not_found",
                "Landing de Flow detectada pero no se encontró botón 'Sign in with Google'",
            )

        log_ok(f"Click en botón Flow sign-in: '{data.get('text', '?')}'")
        return True

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

            # Primero intentar click en cuenta guardada (account chooser).
            # Retorna JSON {strategy, email} para poder matchear luego en el dropdown de passwords.
            clicked_raw = self.evaluate("""(() => {
                const extractEmail = (text) => {
                    const m = (text || '').match(/[\\w.+-]+@[\\w-]+\\.[\\w.-]+/);
                    return m ? m[0] : '';
                };
                const done = (strategy, el) => JSON.stringify({
                    strategy,
                    email: extractEmail(
                        el.getAttribute?.('data-identifier') ||
                        el.getAttribute?.('data-email') ||
                        el.innerText || ''
                    ),
                });

                // Estrategia 1: data-identifier (cuenta guardada)
                const byId = document.querySelector('[data-identifier]');
                if (byId) { byId.click(); return done('data-identifier', byId); }

                // Estrategia 2: primer <li> con email
                const items = document.querySelectorAll('ul li');
                for (const li of items) {
                    const text = li.innerText || '';
                    if (text.includes('@')) { li.click(); return done('email-li', li); }
                }

                // Estrategia 3: div con data-email
                const emailDiv = document.querySelector('[data-email]');
                if (emailDiv) { emailDiv.click(); return done('data-email', emailDiv); }

                // Estrategia 4: cualquier elemento con email visible
                const all = document.querySelectorAll('div, a, button');
                for (const el of all) {
                    const t = (el.innerText || '').trim();
                    if (/@.*\\.com/.test(t) && t.length < 60) { el.click(); return done('email-text', el); }
                }

                return null;
            })()""")

            clicked = None
            selected_email = ""
            if clicked_raw and clicked_raw != "null":
                try:
                    info = json.loads(clicked_raw)
                    clicked = info.get("strategy")
                    selected_email = (info.get("email") or "").strip().lower()
                except Exception:
                    clicked = clicked_raw  # fallback tolerante

            if clicked:
                log_ok(f"Click en cuenta de Google ({clicked}) email={selected_email or '?'}")

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
                    log_info(f"Página de contraseña detectada para {selected_email or '?'}")
                    try:
                        self._handle_password_page(expected_email=selected_email)
                    except Veo3LoginError as exc:
                        log_error(f"Fallo en password step: {exc}")
                        return False

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

    def _handle_password_page(self, expected_email: str = "") -> bool:
        """Autofill de password en la pantalla "Enter your password" de Google.

        Estrategia (opción C — navegación por teclado, sin guardar credenciales):
          1. Esperar a que cargue input[name="Passwd"].
          2. Focus vía CDP en el campo.
          3. Dispatch ArrowDown + Enter → Chrome Password Manager autoselecciona
             la primera sugerencia (que por comportamiento nativo de Chrome
             coincide con el email mostrado en la pantalla previa).
          4. Verificar que el campo se llenó (pwd_len > 0); si no → password_not_saved.
          5. Click en botón "Siguiente" por CDP.
          6. Esperar redirect a labs.google.

        Lanza Veo3LoginError con código clasificado ante cualquier fallo.
        expected_email se usa solo para logging / futuro match si Chrome cambia
        el orden de sugerencias; el dropdown nativo de Chrome no es parte del DOM.
        """
        # 1. Esperar el input con timeout duro de 10s (KISS, sin polling defensivo)
        has_pwd = False
        for _ in range(10):
            has_pwd = bool(self.evaluate(
                "document.readyState === 'complete' && "
                "!!document.querySelector('input[name=\"Passwd\"]')"
            ))
            if has_pwd:
                break
            time.sleep(1)

        if not has_pwd:
            raise Veo3LoginError(
                "password_input_not_found",
                f"input[name='Passwd'] no apareció en 10s (email esperado={expected_email or '?'})",
            )

        # 2. Focus en el campo (necesario para que el password manager de Chrome
        # considere abrir su dropdown de sugerencias nativo).
        self.evaluate("""(() => {
            const pwd = document.querySelector('input[name="Passwd"]');
            if (pwd) { pwd.focus(); pwd.click(); }
        })()""")
        time.sleep(0.5)

        # 3. ArrowDown(x2) → abre el dropdown nativo de Chrome Password Manager
        #    y se asegura de resaltar la primera sugerencia. Algunas builds de
        #    Chrome solo abren el dropdown con el primer ArrowDown sin resaltar
        #    nada; el segundo confirma la primera entrada como activa.
        #    Usamos rawKeyDown para teclas de control (CDP responde mejor que
        #    keyDown para navegación que no genera 'char' events).
        for _ in range(2):
            self._send_raw("Input.dispatchKeyEvent", {
                "type": "rawKeyDown", "key": "ArrowDown", "code": "ArrowDown",
                "windowsVirtualKeyCode": 40, "nativeVirtualKeyCode": 40,
            })
            self._send_raw("Input.dispatchKeyEvent", {
                "type": "keyUp", "key": "ArrowDown", "code": "ArrowDown",
                "windowsVirtualKeyCode": 40, "nativeVirtualKeyCode": 40,
            })
            time.sleep(0.3)

        # Espera larga para que el dropdown nativo termine de hidratar la
        # selección resaltada. 0.8s no era suficiente — captura del usuario
        # mostraba dropdown abierto pero Enter llegaba antes de la hidratación.
        time.sleep(1.5)

        # Refocus del input antes del Enter — el dropdown nativo es un overlay
        # OS-level fuera del DOM y puede haberse llevado el foco efectivo. Si
        # el Enter llega al overlay sin foco en el input, Chrome no commitea
        # el autofill al campo.
        self.evaluate("""(() => {
            const pwd = document.querySelector('input[name="Passwd"]');
            if (pwd) pwd.focus();
        })()""")
        time.sleep(0.2)

        self._send_raw("Input.dispatchKeyEvent", {
            "type": "rawKeyDown", "key": "Enter", "code": "Enter",
            "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13,
        })
        self._send_raw("Input.dispatchKeyEvent", {
            "type": "keyUp", "key": "Enter", "code": "Enter",
            "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13,
        })

        # 4. Verificar que el password manager efectivamente llenó el campo.
        #    Damos hasta 3s para que Chrome propague el autofill al input.
        pwd_len = 0
        for _ in range(6):
            time.sleep(0.5)
            raw = self.evaluate(
                "document.querySelector('input[name=\"Passwd\"]')?.value.length || 0"
            )
            try:
                pwd_len = int(raw or 0)
            except (TypeError, ValueError):
                pwd_len = 0
            if pwd_len > 0:
                break

        if pwd_len == 0:
            # Nota: el Enter dispatchado arriba PUDO haber sido consumido por el
            # formulario (submit con campo vacío). En ese caso Google mostrará
            # error "enter a password" — igualmente clasificamos como not_saved
            # porque la causa raíz es que Chrome no tenía credencial guardada.
            raise Veo3LoginError(
                "password_not_saved",
                f"Chrome Password Manager no rellenó la contraseña para {expected_email or '?'}",
            )

        log_ok(f"Password autofill via CDP keyboard ({pwd_len} chars)")

        # Settle determinista: tras confirmar value.length>0, Google necesita
        # ~500-800ms para hidratar el estado del botón Siguiente de
        # aria-disabled=true → habilitado. 1s es conservador y determinista.
        time.sleep(1.0)

        # 5. Click en "Siguiente / Next / Continuar" via CDP.
        #
        #    Selector basado en el HTML real del botón enviado por el usuario:
        #    <button jsname="LgbsSe" jscontroller="soHxf" type="button">
        #      <div></div><div></div><div></div>
        #      <span jsname="V67aGc">Siguiente</span>
        #    </button>
        #
        #    REGLAS ESTRICTAS (anti bug wrapper contenedor):
        #      - SOLO <button> (tagName === 'BUTTON'). No divs. No [role="button"].
        #      - Texto canónico = SOLO el <span jsname="V67aGc"> interior, o en su
        #        defecto los text nodes DIRECTOS del button (sin recursión). Jamás
        #        innerText/textContent del button (incluyen hijos descendentes y
        #        contaminan con "¿Olvidaste la contraseña?" cuando el matcheo
        #        devolvió un wrapper padre).
        #      - Igualdad EXACTA contra WANTED — nada de includes/startsWith.
        #      - Validación de tamaño: un botón MDC real mide ~40-300 x 20-80.
        #        Un wrapper contenedor del modal es mucho mayor → rechazado.
        #      - Sin fallbacks permisivos. Si nada matchea → null → fail limpio.
        #      - Si hay múltiples candidatos, preferir el más abajo-derecha:
        #        el botón "Siguiente" de Google siempre está en la esquina
        #        inferior derecha del modal (el link "¿Olvidaste?" va a la izq).
        log_ok("Buscando boton 'Siguiente' (match exacto span V67aGc, sin fallbacks permisivos)")
        btn_info = self.evaluate("""(() => {
            const norm = (s) => (s || '')
                .normalize('NFD')
                .replace(/[\\u0300-\\u036f]/g, '')
                .toLowerCase()
                .replace(/[^a-z0-9]+/g, ' ')
                .trim();
            const WANTED = ['next', 'siguiente', 'continuar', 'continue'];
            // Defensa: cualquier button cuyo innerText crudo contenga una de
            // estas palabras es un WRAPPER contaminado (contiene el link de
            // "¿Olvidaste la contraseña?" u otra acción secundaria). Rechazo
            // duro — el selector continuará con el siguiente candidato.
            const BLACKLIST = ['olvidaste', 'forgot', 'help', 'ayuda',
                               'cancel', 'cancelar', 'atras', 'back'];

            // Texto canónico del botón: span jsname="V67aGc" si existe,
            // si no, text nodes DIRECTOS (no recursivos) del button.
            const cleanTextOf = (btn) => {
                const span = btn.querySelector(':scope > span[jsname="V67aGc"]');
                if (span) return (span.textContent || '').trim();
                const direct = Array.from(btn.childNodes)
                    .filter(n => n.nodeType === 3)
                    .map(n => n.nodeValue || '')
                    .join('')
                    .trim();
                return direct;
            };

            // innerText crudo (recursivo) — SOLO para detectar contaminación
            // (blacklist + multi-línea). Nunca se usa como label del click.
            const rawInnerTextOf = (btn) => (btn.innerText || '').toLowerCase();

            const isContaminated = (btn) => {
                const raw = rawInnerTextOf(btn);
                if (!raw) return false;
                // Multi-acción: el button real MDC de Google nunca tiene \\n.
                if (raw.split('\\n').length > 1) return true;
                // Blacklist: wrapper que incluye acciones secundarias.
                for (const w of BLACKLIST) {
                    if (raw.includes(w)) return true;
                }
                return false;
            };

            const isVisibleEnabled = (btn) => {
                if (!btn) return false;
                if (btn.tagName !== 'BUTTON') return false;
                if (btn.offsetParent === null) return false;
                if (btn.disabled === true) return false;
                if (btn.getAttribute('aria-disabled') === 'true') return false;
                return true;
            };

            const describe = (btn, cleanText, priority) => {
                btn.scrollIntoView({block: 'center', inline: 'center'});
                const r = btn.getBoundingClientRect();
                const w = Math.round(r.width);
                const h = Math.round(r.height);
                // Tamaño razonable de un MDC button (no un wrapper contenedor).
                if (w < 40 || w > 300) return null;
                if (h < 20 || h > 80) return null;
                const rawInner = (btn.innerText || '').trim();
                return {
                    cx: Math.round(r.left + r.width / 2),
                    cy: Math.round(r.top + r.height / 2),
                    left: Math.round(r.left),
                    top: Math.round(r.top),
                    width: w,
                    height: h,
                    label: cleanText.slice(0, 40),
                    span_text: cleanText.slice(0, 60),
                    inner_text: rawInner.slice(0, 80).replace(/\\n/g, ' | '),
                    tag: btn.tagName,
                    jsname: btn.getAttribute('jsname') || '',
                    jscontroller: btn.getAttribute('jscontroller') || '',
                    priority: priority,
                };
            };

            const candidates = [];

            const rejected = [];  // diagnóstico de por qué descartamos

            // Prioridad 1: <button> con texto exacto en span V67aGc o text nodes directos.
            for (const b of document.querySelectorAll('button')) {
                if (!isVisibleEnabled(b)) continue;
                const clean = cleanTextOf(b);
                if (!clean) continue;
                const n = norm(clean);
                if (!WANTED.includes(n)) continue;  // IGUALDAD EXACTA
                if (isContaminated(b)) {
                    rejected.push({
                        why: 'contaminated',
                        span_text: clean.slice(0, 40),
                        inner_text: (b.innerText || '').trim().slice(0, 80).replace(/\\n/g, ' | '),
                    });
                    continue;
                }
                const info = describe(b, clean, 1);
                if (info) candidates.push(info);
            }

            // Prioridad 2: span[jsname="V67aGc"] con texto exacto → su button ancestro.
            if (candidates.length === 0) {
                const seen = new Set();
                for (const span of document.querySelectorAll('button > span[jsname="V67aGc"]')) {
                    const btn = span.closest('button');
                    if (!btn || seen.has(btn)) continue;
                    seen.add(btn);
                    if (!isVisibleEnabled(btn)) continue;
                    const clean = (span.textContent || '').trim();
                    if (!clean) continue;
                    const n = norm(clean);
                    if (!WANTED.includes(n)) continue;  // IGUALDAD EXACTA
                    if (isContaminated(btn)) {
                        rejected.push({
                            why: 'contaminated',
                            span_text: clean.slice(0, 40),
                            inner_text: (btn.innerText || '').trim().slice(0, 80).replace(/\\n/g, ' | '),
                        });
                        continue;
                    }
                    const info = describe(btn, clean, 2);
                    if (info) candidates.push(info);
                }
            }

            // NO hay prioridad 3. Sin fallback por [role="button"] / includes.

            if (candidates.length === 0) {
                // Diagnóstico: primeros 5 textos de buttons visibles para debug.
                const visibleBtns = Array.from(document.querySelectorAll('button'))
                    .filter(b => b.offsetParent !== null);
                const firstTexts = visibleBtns.slice(0, 5).map(b => {
                    const c = cleanTextOf(b);
                    return c.slice(0, 30);
                });
                return JSON.stringify({
                    found: false,
                    visible_buttons: visibleBtns.length,
                    first_texts: firstTexts,
                    rejected: rejected,
                });
            }

            // Ranking: preferir más a la derecha y más abajo
            // (botón Siguiente siempre en esquina inf-derecha del modal).
            candidates.sort((a, b) => {
                const sa = a.left + a.top * 0.5;
                const sb = b.left + b.top * 0.5;
                return sb - sa;
            });

            return JSON.stringify({
                found: true,
                winner: candidates[0],
                all: candidates.map(c => ({
                    label: c.label, jsname: c.jsname, cx: c.cx, cy: c.cy,
                    w: c.width, h: c.height, prio: c.priority,
                })),
                rejected: rejected,
            });
        })()""")

        # Parseo del resultado. El selector devuelve SIEMPRE JSON con
        # {found: bool, ...}. null solo si evaluate() falló a nivel CDP.
        parsed = None
        if btn_info and btn_info != "null":
            try:
                parsed = json.loads(btn_info)
            except Exception as exc:
                raise Veo3LoginError(
                    "next_button_click_failed",
                    f"No se pudo parsear respuesta del selector Siguiente: {exc}",
                ) from exc

        if not parsed or not parsed.get("found"):
            visible_count = 0
            first_texts: list = []
            if parsed:
                visible_count = parsed.get("visible_buttons", 0)
                first_texts = parsed.get("first_texts", [])
            # Puede ser que la página ya navegó (Enter en paso 3 hizo submit).
            still_here = self.evaluate(
                "!!document.querySelector('input[name=\"Passwd\"]') && "
                "(window.location.href || '').toLowerCase().includes('accounts.google.com')"
            )
            rejected = parsed.get("rejected", []) if parsed else []
            log_warn(
                f"Siguiente no encontrado. Total buttons visibles: {visible_count}. "
                f"Textos: {first_texts}. Rechazados: {rejected}"
            )
            if still_here:
                raise Veo3LoginError(
                    "next_button_not_found",
                    "Password autofilleado pero no se encontró botón Siguiente/Next",
                )
            log_info("Botón Siguiente no hallado pero página ya navegó — OK")
        else:
            info = parsed["winner"]
            cx = int(info["cx"])
            cy = int(info["cy"])
            label = info.get("label", "")
            span_text = info.get("span_text", "")
            inner_text = info.get("inner_text", "")
            width = info.get("width", 0)
            height = info.get("height", 0)
            tag = info.get("tag", "")
            jsname_attr = info.get("jsname", "")
            priority = info.get("priority", 0)

            all_cands = parsed.get("all", [])
            if len(all_cands) > 1:
                log_info(f"Candidatos Siguiente: {json.dumps(all_cands)}")
            rejected_cands = parsed.get("rejected", [])
            if rejected_cands:
                log_info(f"Candidatos rechazados por contaminacion: {json.dumps(rejected_cands)}")

            log_ok(
                f"Boton encontrado (prio={priority}): tag={tag} jsname={jsname_attr} "
                f"span_text='{span_text}' inner_text='{inner_text}' "
                f"rect=({cx},{cy}) size={width}x{height}"
            )

            # Click físico real via CDP: mousePressed + mouseReleased sobre el
            # centro del botón. Dispara mousedown/mouseup/click en el orden
            # correcto — necesario para botones Google que escuchan mousedown.
            log_ok(f"Click CDP mousePressed en ({cx},{cy})")
            press = self._send_raw("Input.dispatchMouseEvent", {
                "type": "mousePressed", "x": cx, "y": cy,
                "button": "left", "clickCount": 1,
            })
            release = self._send_raw("Input.dispatchMouseEvent", {
                "type": "mouseReleased", "x": cx, "y": cy,
                "button": "left", "clickCount": 1,
            })
            if press is None or release is None:
                log_error(f"dispatchMouseEvent fallo al clickear ({cx},{cy})")
                raise Veo3LoginError(
                    "next_button_click_failed",
                    f"CDP dispatchMouseEvent falló sobre botón '{label}' en ({cx},{cy})",
                )
            log_ok("Click CDP mouseReleased disparado, esperando navegacion")

        # 6. Esperar redirect fuera de accounts.google.com (hasta 15s).
        for _ in range(15):
            time.sleep(1)
            url_now = (self.evaluate("window.location.href") or "").lower()
            if url_now and "accounts.google.com" not in url_now:
                log_ok(f"Password flow OK → {url_now[:60]}")
                return True

        raise Veo3LoginError(
            "google_flow_redirect_timeout",
            "Tras click en Siguiente, Google no redirigió fuera de accounts.google.com en 15s",
        )

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
    """DEPRECATED wrapper — ahora delega al selector con prioridad.

    Mantenido para compatibilidad con navigate_and_stabilize. Selecciona
    el tab correcto (Flow > accounts.google > otro > createTarget) y cierra
    los demás. Retorna el webSocketDebuggerUrl del tab elegido.
    """
    tmp = Veo3Session(port=port)
    selected = tmp._select_or_create_flow_tab()
    if not selected:
        log_warn(f"Sin tabs en puerto {port}")
        return ""
    keep_id = selected.get("id", "")
    if keep_id:
        tmp._close_other_tabs(keep_id)
    return selected.get("webSocketDebuggerUrl", "")


def _ensure_on_flow(session: Veo3Session) -> dict:
    """Garantiza que la sesión termine en labs.google/fx (sesión activa).

    Flujo (KISS, sin retries, sin polling defensivo, timeouts duros):
      1. Navegar a VEO3_URL.
      2. wait_for_page_load (20s hard timeout).
      3. detect_page_state:
         - 'flow'            → OK, continuar.
         - 'account_chooser' → click en primera cuenta, esperar carga,
                                recursión única: si cae en 'password',
                                _handle_password_page; si cae en 'flow', OK.
         - 'password'        → _handle_password_page directamente.
         - 'sign_in'         → error 'login_state_unknown' (no hay flujo
                                de email+password implementado aquí; ese
                                camino lo cubre el login manual o una
                                iteración futura).
         - 'unknown'         → error 'login_state_unknown'.

    Retorna dict con 'success' bool y 'error' (código clasificado) en caso
    de fallo. Propaga códigos de Veo3LoginError.
    """
    # Early-return: si la pestaña actual YA está en Flow (sesión activa,
    # ej. perfil abierto en labs.google/fx/tools/flow/project/xxx), NO
    # navegar — la navigate a la home puede tirar al perfil al login flow
    # innecesariamente y marcar el perfil como expired aunque sea válido.
    # Importante: verificar con detect_page_state que NO sea landing
    # (flow_signin_prompt) — la URL puede ser labs.google/fx pero tener
    # el botón "Sign in with Google" visible (sesión NO activa).
    current_url = (session.evaluate("window.location.href") or "").lower()
    if "labs.google/fx" in current_url and "accounts.google" not in current_url:
        current_state = session.detect_page_state()
        if current_state.get("state") == "flow":
            log_ok(f"Sesión activa detectada sin navegar — url={current_url[:80]}")
            return {"success": True}
        log_info(
            f"URL Flow pero estado={current_state.get('state')} — procediendo con login flow"
        )

    log_info(f"Navegando a {VEO3_URL}...")
    session.navigate(VEO3_URL)

    if not session.wait_for_page_load(timeout_sec=20):
        return {"success": False, "error": "page_load_timeout",
                "details": "document.readyState no llegó a 'complete' en 20s"}

    state = session.detect_page_state()
    log_info(f"Estado detectado: {state.get('state')} url={state.get('url', '')[:80]}")

    if state["state"] == "flow":
        log_ok("Sesión activa — ya en Flow")
        return {"success": True}

    if state["state"] == "flow_signin_prompt":
        log_info("Landing de Flow detectada — click en 'Sign in with Google'")
        try:
            session.click_flow_signin_button()
        except Veo3LoginError as exc:
            return {"success": False, "error": exc.code, "details": str(exc)}

        if not session.wait_for_page_load(timeout_sec=20):
            return {"success": False, "error": "page_load_timeout",
                    "details": "Post-click flow_signin: readyState no completó"}

        # Re-clasificar: debería caer en account_chooser (mayoría) o password
        # o flow (si Google reusa sesión silenciosamente).
        state = session.detect_page_state()
        log_info(f"Estado post flow_signin: {state.get('state')} url={state.get('url', '')[:80]}")

        if state["state"] == "flow":
            log_ok("Sesión activa tras click en Sign in")
            return {"success": True}
        # Si no es flow, cae natural en los dispatches de abajo
        # (account_chooser / password / sign_in / unknown).

    if state["state"] == "account_chooser":
        if state.get("identifier_count", 0) == 0:
            return {"success": False, "error": "account_chooser_no_accounts",
                    "details": "Pantalla account chooser sin [data-identifier]"}
        email = session.click_account_chooser()
        log_info(f"Click en cuenta del chooser: {email}")

        # Esperar a que la navegación post-click termine.
        if not session.wait_for_page_load(timeout_sec=20):
            return {"success": False, "error": "page_load_timeout",
                    "details": "Post-click account chooser: readyState no completó"}

        state2 = session.detect_page_state()
        log_info(f"Estado post-click: {state2.get('state')}")

        if state2["state"] == "flow":
            return {"success": True}
        if state2["state"] == "password":
            try:
                session._handle_password_page(expected_email=email.lower())
            except Veo3LoginError as exc:
                return {"success": False, "error": exc.code, "details": str(exc)}
            # Tras password_page, _handle_password_page ya esperó redirect
            # fuera de accounts.google — asumimos Flow (wait_for_page_load
            # por seguridad).
            session.wait_for_page_load(timeout_sec=15)
            if session.is_on_flow():
                return {"success": True}
            return {"success": False, "error": "google_flow_redirect_timeout",
                    "details": "Password OK pero no aterrizó en Flow"}

        return {"success": False, "error": "login_state_unknown",
                "details": f"Post account_chooser: state={state2.get('state')}"}

    if state["state"] == "password":
        try:
            session._handle_password_page(expected_email="")
        except Veo3LoginError as exc:
            return {"success": False, "error": exc.code, "details": str(exc)}
        session.wait_for_page_load(timeout_sec=15)
        if session.is_on_flow():
            return {"success": True}
        return {"success": False, "error": "google_flow_redirect_timeout",
                "details": "Password OK pero no aterrizó en Flow"}

    return {"success": False, "error": "login_state_unknown",
            "details": f"state={state.get('state')} url={state.get('url', '')[:120]}"}


def open_new_project(port: int, prompt: str = "") -> dict:
    """Abre un nuevo proyecto (chat) en Flow y opcionalmente envía un prompt."""
    session = Veo3Session(port=port)
    if not session.connect():
        return {"success": False, "error": f"No se pudo conectar en puerto {port}"}

    # Buffer de logs del proceso de login, re-emitido al cliente HTTP via
    # campo 'login_logs' para que aparezca en el log del caller (Huey), sin
    # necesidad de mirar el stdout del server-dicloak.
    login_logs: list[str] = []
    try:
        # Navegar y garantizar Flow (sesión activa, account_chooser, o password).
        if not session.is_on_flow():
            with capture_logs() as _buf:
                try:
                    ensured = _ensure_on_flow(session)
                finally:
                    login_logs = list(_buf)
            if not ensured.get("success"):
                return {
                    "success": False,
                    "error": ensured.get("error", "no_flow"),
                    "details": ensured.get("details", ""),
                    "url": session.evaluate("window.location.href") or "",
                    "login_logs": login_logs,
                }
            # Un pequeño settle tras login para que Flow termine de hidratar.
            time.sleep(2)

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

        # Asegurar modo Video antes de pegar prompt
        if not ensure_video_mode(session):
            log_warn("No se pudo asegurar modo Video, intentando enviar de todas formas...")

        # Si hay prompt, pegarlo y enviarlo
        prompt_sent = False
        if prompt:
            prompt_sent = _paste_and_send_prompt(session, prompt)

        if prompt and not prompt_sent:
            return {
                "success": False,
                "error": "No se pudo enviar el prompt despues de 2 intentos (modelo bloqueado)",
                "needs_account_switch": True,
                "port": port,
                "url": project_url or url,
                "login_logs": login_logs,
            }

        return {
            "success": True,
            "port": port,
            "url": project_url or url,
            "title": title,
            "prompt_sent": prompt_sent,
            "login_logs": login_logs,
        }

    finally:
        session.close()


def ensure_video_mode(session: Veo3Session) -> bool:
    """Verifica que el selector de modelo esté en Video. Si está en Imagen, cambia.

    Flujo:
      1. Click en el chip del modelo (Nano Banana / Veo) → abre dropdown
      2. Verificar aria-selected del tab Video
      3. Si Imagen está seleccionado → click CDP real en tab Video
      4. Cerrar dropdown clickeando fuera

    Retorna True si ya estaba en Video o se cambió exitosamente.
    """
    # 1. Verificar estado actual sin abrir dropdown
    current = session.evaluate("""(() => {
        const btns = Array.from(document.querySelectorAll('button, [role="button"]'));
        for (const b of btns) {
            if (!b.offsetParent) continue;
            const text = (b.innerText || '').trim();
            const rect = b.getBoundingClientRect();
            // Chip del modelo: está en la parte inferior y contiene info del modelo
            if (rect.top > window.innerHeight * 0.5 && rect.width > 80
                && (text.includes('Nano Banana') || text.includes('nano banana')
                    || text.includes('Veo') || text.includes('veo')
                    || text.includes('x2') || text.includes('x1'))) {
                return JSON.stringify({
                    text: text.replace(/\\n/g, ' ').substring(0, 60),
                    x: Math.round(rect.x + rect.width / 2),
                    y: Math.round(rect.y + rect.height / 2),
                });
            }
        }
        return 'NO_SELECTOR';
    })()""")

    if not current or current == "NO_SELECTOR":
        log_warn("[VIDEO_MODE] No se encontró selector de modelo")
        return False

    try:
        chip = json.loads(current)
    except Exception:
        log_warn(f"[VIDEO_MODE] Error parseando selector: {current}")
        return False

    log_info(f"[VIDEO_MODE] Selector actual: {chip['text']}")

    # 2. Abrir dropdown con CDP click real
    session._send_raw("Input.dispatchMouseEvent", {
        "type": "mouseMoved", "x": chip["x"], "y": chip["y"],
    })
    time.sleep(0.05)
    session._send_raw("Input.dispatchMouseEvent", {
        "type": "mousePressed", "x": chip["x"], "y": chip["y"],
        "button": "left", "clickCount": 1,
    })
    time.sleep(0.05)
    session._send_raw("Input.dispatchMouseEvent", {
        "type": "mouseReleased", "x": chip["x"], "y": chip["y"],
        "button": "left", "clickCount": 1,
    })
    time.sleep(1.5)

    # 3. Verificar si hay tab Video y su estado
    tab_state = session.evaluate("""(() => {
        const tabs = document.querySelectorAll('[role="tab"]');
        let videoTab = null;
        let imageSelected = false;
        let videoSelected = false;

        for (const t of tabs) {
            const text = (t.innerText || '').trim().toLowerCase();
            if (text.includes('imagen') || text.includes('image')) {
                if (t.getAttribute('aria-selected') === 'true') imageSelected = true;
            }
            if (text.includes('deo') || text.includes('video')) {
                videoSelected = t.getAttribute('aria-selected') === 'true';
                if (!videoSelected) {
                    const rect = t.getBoundingClientRect();
                    videoTab = {x: Math.round(rect.x + rect.width / 2), y: Math.round(rect.y + rect.height / 2)};
                }
            }
        }

        if (videoSelected) return JSON.stringify({status: 'ALREADY_VIDEO'});
        if (videoTab) return JSON.stringify({status: 'NEEDS_CLICK', ...videoTab});
        return JSON.stringify({status: 'NO_TABS'});
    })()""")

    try:
        state = json.loads(tab_state)
    except Exception:
        log_warn(f"[VIDEO_MODE] Error parseando tabs: {tab_state}")
        return False

    if state["status"] == "ALREADY_VIDEO":
        log_ok("[VIDEO_MODE] Ya está en modo Video")
        # Cerrar dropdown clickeando fuera
        session._send_raw("Input.dispatchMouseEvent", {
            "type": "mousePressed", "x": 100, "y": 100,
            "button": "left", "clickCount": 1,
        })
        session._send_raw("Input.dispatchMouseEvent", {
            "type": "mouseReleased", "x": 100, "y": 100,
            "button": "left", "clickCount": 1,
        })
        time.sleep(0.5)
        return True

    if state["status"] == "NO_TABS":
        log_warn("[VIDEO_MODE] No se encontraron tabs Image/Video en el dropdown")
        return False

    # 4. Click CDP real en tab Video
    log_info(f"[VIDEO_MODE] Clickeando tab Video en ({state['x']}, {state['y']})...")
    session._send_raw("Input.dispatchMouseEvent", {
        "type": "mouseMoved", "x": state["x"], "y": state["y"],
    })
    time.sleep(0.05)
    session._send_raw("Input.dispatchMouseEvent", {
        "type": "mousePressed", "x": state["x"], "y": state["y"],
        "button": "left", "clickCount": 1,
    })
    time.sleep(0.05)
    session._send_raw("Input.dispatchMouseEvent", {
        "type": "mouseReleased", "x": state["x"], "y": state["y"],
        "button": "left", "clickCount": 1,
    })
    time.sleep(1)

    # 5. Verificar que cambió
    verify = session.evaluate("""(() => {
        const tabs = document.querySelectorAll('[role="tab"]');
        for (const t of tabs) {
            const text = (t.innerText || '').trim().toLowerCase();
            if ((text.includes('deo') || text.includes('video'))
                && t.getAttribute('aria-selected') === 'true')
                return 'VIDEO_OK';
        }
        return 'NOT_VIDEO';
    })()""")

    if verify == "VIDEO_OK":
        log_ok("[VIDEO_MODE] Cambiado a modo Video exitosamente")
        # Cerrar dropdown
        session._send_raw("Input.dispatchMouseEvent", {
            "type": "mousePressed", "x": 100, "y": 100,
            "button": "left", "clickCount": 1,
        })
        session._send_raw("Input.dispatchMouseEvent", {
            "type": "mouseReleased", "x": 100, "y": 100,
            "button": "left", "clickCount": 1,
        })
        time.sleep(0.5)
        return True

    log_warn(f"[VIDEO_MODE] No se pudo cambiar a Video: {verify}")
    return False


def _is_send_blocked(session: Veo3Session) -> bool:
    """Detecta si el boton de envio esta bloqueado o el selector esta en Nano Banana."""
    result = session.evaluate("""(() => {
        // Verificar si el selector esta en Nano Banana (modelo de imagen, no video)
        const selectorBtns = Array.from(document.querySelectorAll('button, [role="button"]'));
        for (const btn of selectorBtns) {
            if (!btn.offsetParent) continue;
            const text = (btn.innerText || '').toLowerCase();
            const rect = btn.getBoundingClientRect();
            if (rect.top > window.innerHeight * 0.5
                && (text.includes('nano') || text.includes('banana'))) {
                return 'WRONG_MODEL';
            }
        }

        const btns = Array.from(document.querySelectorAll('button'));
        const sendBtn = btns.find(b => {
            const text = (b.innerText || '').toLowerCase();
            return (text.includes('arrow_forward') || text.includes('send'))
                && b.getBoundingClientRect().top > window.innerHeight * 0.5;
        });
        if (!sendBtn) return 'NO_BUTTON';
        if (sendBtn.disabled) return 'DISABLED';
        const html = (sendBtn.innerHTML || '').toLowerCase();
        if (html.includes('stop_circle') || html.includes('#ef6c00')
            || html.includes('#e65100') || html.includes('orange')
            || html.includes('block')) return 'BLOCKED';
        return 'OK';
    })()""")
    blocked = result in ("DISABLED", "BLOCKED", "NO_BUTTON", "WRONG_MODEL")
    if result == "WRONG_MODEL":
        log_warn("[ENVIO] Selector en Nano Banana (imagen). Necesita cambiar a Video.")
    return blocked


def _switch_to_lower_priority(session: Veo3Session) -> bool:
    """Cambia el modelo a Video con Lower Priority.

    Flujo real de la UI (basado en screenshots):
    1. Click en el selector del modelo (dice "Nano Banana 2 x2" o "Video x2")
       → abre popup con tabs Image/Video, ratios, multiplicadores, y dropdown de modelo
    2. Si el tab activo es Image → click en tab "Video"
    3. Click en dropdown del modelo (dice "Nano Banana 2" o "Veo 3.1 - Fast")
       → abre lista: Veo 3.1 - Lite, Fast, Fast [Lower Priority], Quality
    4. Click en "Veo 3.1 - Fast [Lower Priority]"
    """
    log_info("[MODELO] Iniciando cambio a Video Lower Priority...")

    # Paso 1: Click en el selector del modelo (boton en la parte inferior)
    log_info("[MODELO] Paso 1: Abriendo selector de modelo...")
    session.evaluate("""(() => {
        const btns = Array.from(document.querySelectorAll('button, [role="button"]'));
        for (const btn of btns) {
            if (!btn.offsetParent) continue;
            const text = (btn.innerText || '').toLowerCase();
            const rect = btn.getBoundingClientRect();
            if (rect.top > window.innerHeight * 0.5 && rect.width > 80
                && (text.includes('nano') || text.includes('banana')
                    || text.includes('video') || text.includes('veo')
                    || text.includes('x2') || text.includes('x1'))) {
                btn.click();
                return;
            }
        }
    })()""")
    time.sleep(2)

    # Paso 2: Verificar si hay tab "Video" y clickearlo
    # El popup tiene tabs: "Image" / "Video" (o "Imagen" / "Vídeo")
    log_info("[MODELO] Paso 2: Buscando tab Video...")
    clicked_video_tab = session.evaluate("""(() => {
        const all = Array.from(document.querySelectorAll('button, [role="tab"], div, span'));
        for (const el of all) {
            if (!el.offsetParent) continue;
            const text = (el.innerText || el.textContent || '').trim().toLowerCase();
            const rect = el.getBoundingClientRect();
            // Tab "Video" / "Vídeo" — debe ser un elemento pequeno (tab), no un boton grande
            if ((text === 'video' || text === 'vídeo')
                && rect.width > 40 && rect.width < 200 && rect.height > 20 && rect.height < 60) {
                el.click();
                return 'CLICKED: ' + text;
            }
        }
        return 'NOT_FOUND';
    })()""")

    if clicked_video_tab and "CLICKED" in str(clicked_video_tab):
        log_ok(f"[MODELO] Tab Video seleccionado: {clicked_video_tab}")
    else:
        log_info("[MODELO] Tab Video no encontrado (puede que ya este en Video)")
    time.sleep(2)

    # Paso 3: Click en el dropdown del modelo (dice "Veo 3.1 - Fast" o "Nano Banana 2")
    # Es un dropdown/select DENTRO del popup, no el boton principal
    log_info("[MODELO] Paso 3: Abriendo dropdown de modelo...")
    clicked_dropdown = session.evaluate("""(() => {
        const all = Array.from(document.querySelectorAll(
            'button, [role="button"], [role="listbox"], [role="combobox"], select'
        ));
        for (const el of all) {
            if (!el.offsetParent) continue;
            const text = (el.innerText || '').toLowerCase();
            const rect = el.getBoundingClientRect();
            // Dropdown del modelo: contiene "veo" o "nano" o "fast" o "lite"
            // y tiene un icono de flecha (arrow_drop_down)
            if (rect.width > 100 && rect.height > 30 && rect.height < 60
                && (text.includes('veo') || text.includes('nano') || text.includes('banana')
                    || text.includes('fast') || text.includes('lite') || text.includes('quality'))) {
                el.click();
                return 'CLICKED: ' + text.replace(/\\n/g, ' ').substring(0, 40);
            }
        }
        return 'NOT_FOUND';
    })()""")

    if not clicked_dropdown or "CLICKED" not in str(clicked_dropdown):
        log_warn(f"[MODELO] Dropdown de modelo no encontrado: {clicked_dropdown}")
        return False
    log_ok(f"[MODELO] Dropdown abierto: {clicked_dropdown}")
    time.sleep(2)

    # Debug: listar opciones del dropdown
    options = session.evaluate("""(() => {
        const items = Array.from(document.querySelectorAll('*')).filter(el => {
            if (!el.offsetParent) return false;
            const text = (el.innerText || '').toLowerCase();
            const rect = el.getBoundingClientRect();
            return rect.width > 100 && rect.height > 30 && rect.height < 60
                && (text.includes('veo') || text.includes('lower') || text.includes('lite')
                    || text.includes('fast') || text.includes('quality'));
        });
        return JSON.stringify(items.map(el => ({
            text: (el.innerText || '').trim().substring(0, 50),
            tag: el.tagName,
        })));
    })()""")
    log_info(f"[MODELO] Opciones del dropdown: {options}")

    # Paso 4: Click en "Lower Priority"
    log_info("[MODELO] Paso 4: Seleccionando Lower Priority...")
    selected = session.evaluate("""(() => {
        const items = Array.from(document.querySelectorAll('*')).filter(el => {
            if (!el.offsetParent) return false;
            const rect = el.getBoundingClientRect();
            return rect.width > 80 && rect.height > 25;
        });

        // Buscar "Lower Priority" exacto
        for (const item of items) {
            const text = (item.innerText || item.textContent || '').toLowerCase();
            if (text.includes('lower priority')) {
                item.click();
                return 'CLICKED: ' + (item.innerText || '').trim().substring(0, 50);
            }
        }
        return 'NOT_FOUND';
    })()""")

    if selected and "CLICKED" in str(selected):
        log_ok(f"[MODELO] Modelo cambiado: {selected}")
        time.sleep(2)
        return True

    log_warn(f"[MODELO] No se encontro opcion lower priority: {selected}")
    return False


def _ensure_video_lower_priority(session: Veo3Session) -> bool:
    """Configura el modelo a Veo 3.1 Fast [Lower Priority] ANTES de pegar/enviar.

    Funciona en 2 contextos:
    - New project: selector "Nano Banana 2 x2" → popup con tabs Image/Video → dropdown
    - Extension: selector "Veo 3.1 - Lite" → dropdown directo (sin popup con tabs)
    """
    # Verificar si ya esta en Lower Priority
    current = session.evaluate("""(() => {
        const btns = Array.from(document.querySelectorAll('button, [role="button"]'));
        for (const b of btns) {
            if (!b.offsetParent) continue;
            const text = (b.innerText || '').toLowerCase();
            if (text.includes('lower priority')) return 'ALREADY_OK';
            if (b.getBoundingClientRect().top > window.innerHeight * 0.5
                && (text.includes('veo') || text.includes('nano') || text.includes('banana')
                    || text.includes('x2') || text.includes('x1') || text.includes('lite')
                    || text.includes('fast') || text.includes('quality'))) {
                return 'NEEDS_CHANGE: ' + text.replace(/\\n/g, ' ').substring(0, 40);
            }
        }
        return 'NO_SELECTOR';
    })()""")
    log_info(f"[MODELO] Estado actual: {current}")

    if current and "ALREADY_OK" in str(current):
        log_ok("[MODELO] Ya esta en Lower Priority")
        return True

    log_info("[MODELO] Cambiando a Video + Lower Priority...")

    # Paso 1: Click en el selector del modelo
    session.evaluate("""(() => {
        const btns = Array.from(document.querySelectorAll('button, [role="button"]'));
        for (const b of btns) {
            if (!b.offsetParent) continue;
            const text = (b.innerText || '').toLowerCase();
            const rect = b.getBoundingClientRect();
            if (rect.top > window.innerHeight * 0.5
                && (text.includes('x2') || text.includes('x1') || text.includes('x3')
                    || text.includes('banana') || text.includes('nano')
                    || text.includes('veo') || text.includes('lite')
                    || text.includes('fast') || text.includes('quality')
                    || text.includes('video') || text.includes('vídeo'))) {
                b.click();
                return;
            }
        }
    })()""")
    time.sleep(2)

    # Paso 2: Si hay tab "Video" clickearlo (solo en new-project, no en extension)
    session.evaluate("""(() => {
        const all = Array.from(document.querySelectorAll('*'));
        for (const el of all) {
            if (!el.offsetParent) continue;
            const text = (el.innerText || el.textContent || '').trim().toLowerCase();
            const rect = el.getBoundingClientRect();
            if ((text === 'video' || text === 'vídeo')
                && rect.width > 40 && rect.width < 200
                && rect.height > 15 && rect.height < 60
                && el.children.length <= 3) {
                el.click();
                return;
            }
        }
    })()""")
    time.sleep(2)

    # Paso 3: Click en el dropdown del modelo (puede ser dentro del popup o directo)
    session.evaluate("""(() => {
        const all = Array.from(document.querySelectorAll('*'));
        for (const el of all) {
            if (!el.offsetParent) continue;
            const text = (el.innerText || '').toLowerCase();
            const rect = el.getBoundingClientRect();
            if (rect.width > 150 && rect.height > 25 && rect.height < 60
                && (text.includes('veo 3') || text.includes('nano banana')
                    || text.includes('fast') || text.includes('lite') || text.includes('quality'))
                && !text.includes('lower priority')) {
                el.click();
                return;
            }
        }
    })()""")
    time.sleep(2)

    # Paso 4: Click en "Lower Priority"
    selected = session.evaluate("""(() => {
        const all = Array.from(document.querySelectorAll('*'));
        for (const el of all) {
            if (!el.offsetParent) continue;
            const text = (el.innerText || el.textContent || '');
            if (text.toLowerCase().includes('lower priority') && text.length < 60) {
                el.click();
                return 'CLICKED: ' + text.trim().substring(0, 50);
            }
        }
        return 'NOT_FOUND';
    })()""")

    if selected and "CLICKED" in str(selected):
        log_ok(f"[MODELO] Lower Priority seleccionado: {selected}")
    else:
        log_warn(f"[MODELO] Lower Priority no encontrado: {selected}")
    time.sleep(1)

    # Cerrar popup
    session.evaluate("document.querySelector('[contenteditable=\"true\"]')?.click()")
    time.sleep(0.5)

    return selected and "CLICKED" in str(selected)


def _paste_and_send_prompt(session: Veo3Session, prompt: str) -> bool:
    """Pega un prompt en el chat de Flow y lo envía.
    Antes de pegar, configura Video + Lower Priority.
    """
    # Paso 0: Configurar modelo a Video + Lower Priority
    _ensure_video_lower_priority(session)

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

    # 5. Intentar enviar (hasta 2 intentos: normal + lower priority)
    for attempt in range(2):
        attempt_label = "1er intento" if attempt == 0 else "2do intento (lower priority)"
        log_info(f"[ENVIO] {attempt_label}...")

        # Verificar si esta bloqueado antes de intentar
        if _is_send_blocked(session):
            log_warn(f"[ENVIO] Envio bloqueado. Cambiando a lower priority...")
            _switch_to_lower_priority(session)
            time.sleep(2)

        # Click en boton de envio
        sent = session.evaluate("""(() => {
            const btns = Array.from(document.querySelectorAll('button')).filter(b => {
                if (!b.offsetParent) return false;
                return b.getBoundingClientRect().top > window.innerHeight * 0.5;
            });

            let sendBtn = btns.find(b => (b.innerText || '').includes('arrow_forward'));

            if (!sendBtn) {
                sendBtn = btns.find(b => {
                    const rect = b.getBoundingClientRect();
                    const hasSvg = !!b.querySelector('svg, [class*="icon"]');
                    return hasSvg && rect.width >= 24 && rect.width <= 60
                        && rect.height >= 24 && rect.height <= 60
                        && rect.left > window.innerWidth * 0.5;
                });
            }

            if (!sendBtn) {
                sendBtn = btns.find(b => {
                    const aria = (b.getAttribute('aria-label') || '').toLowerCase();
                    return aria.includes('send') || aria.includes('submit')
                        || aria.includes('create') || aria.includes('generar')
                        || aria.includes('enviar');
                });
            }

            if (sendBtn && !sendBtn.disabled) {
                sendBtn.click();
                return 'SENT: ' + (sendBtn.innerText || sendBtn.getAttribute('aria-label') || 'btn').substring(0, 20);
            }
            if (sendBtn && sendBtn.disabled) {
                return 'DISABLED: ' + (sendBtn.innerText || '').substring(0, 20);
            }
            return 'NO_BUTTON';
        })()""")

        log_info(f"[ENVIO] Resultado: {sent}")

        if sent and "SENT" in str(sent):
            log_ok(f"[ENVIO] Prompt enviado: {sent}")
            return True

        # Primer intento fallo — cambiar a lower priority y reintentar
        if attempt == 0:
            log_warn(f"[ENVIO] Fallo ({sent}). Cambiando a lower priority para reintentar...")
            _switch_to_lower_priority(session)
            time.sleep(3)

    # Ambos intentos fallaron
    log_error(f"[ENVIO] No se pudo enviar despues de 2 intentos: {sent}")
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

    # 1. Conectar — connect() ya selecciona el tab correcto (Flow >
    #    accounts.google > otro > createTarget) y cierra los demás.
    session = Veo3Session(port=port)
    if not session.connect():
        return {"success": False, "error": f"No se pudo conectar en puerto {port}"}

    time.sleep(1)

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
