"""
ChatGPT Prompt Paste — Pega y envía prompts en ChatGPT via CDP.

Usa WebSocket CDP directo (sin Playwright, sin Node.js).
Soporta múltiples sesiones simultáneas en diferentes puertos.

Cross-platform: Windows, Mac, Linux.
"""
from __future__ import annotations

import json
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Optional

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from logger import log_info, log_ok, log_warn, log_error
from chat_gpt_consulta.account_state import (
    get_exhausted_ids, mark_exhausted, clear_exhausted,
)


@dataclass
class ChatGPTSession:
    """Sesión CDP activa con una instancia de ChatGPT."""
    port: int
    ws_url: str = ""
    _ws: object = field(default=None, repr=False)
    _msg_id: int = field(default=0, repr=False)

    def connect(self) -> bool:
        """Conecta al CDP del navegador de ChatGPT."""
        try:
            import websockets.sync.client as ws_sync
        except ImportError:
            import subprocess
            subprocess.check_call([sys.executable, "-m", "pip", "install", "websockets", "-q"])
            import websockets.sync.client as ws_sync

        # Buscar la página de ChatGPT entre los targets
        self.ws_url = self._find_chatgpt_target()
        if not self.ws_url:
            log_warn(f"No se encontró página de ChatGPT en puerto {self.port}")
            return False

        try:
            self._ws = ws_sync.connect(self.ws_url, max_size=2**22)
            log_ok(f"ChatGPT conectado en puerto {self.port}")
            return True
        except Exception as e:
            log_warn(f"Error conectando a ChatGPT: {e}")
            self._ws = None
            return False

    def _find_chatgpt_target(self) -> str:
        """Busca la página de ChatGPT entre los targets CDP."""
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json", timeout=3) as resp:
                targets = json.loads(resp.read().decode("utf-8"))
            for t in targets:
                url = (t.get("url") or "").lower()
                if "chatgpt.com" in url and t.get("type") == "page":
                    return t.get("webSocketDebuggerUrl", "")
            log_warn(f"No hay página chatgpt.com entre {len(targets)} targets en puerto {self.port}")
        except Exception as e:
            log_warn(f"No se pudo conectar a CDP en puerto {self.port}: {e}")
        return ""

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
        """Evalúa JavaScript en la página de ChatGPT."""
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

    def _send_raw(self, method: str, params: dict | None = None) -> dict | None:
        """Envía comando CDP raw (Input.insertText, Input.dispatchKeyEvent, etc.)."""
        if not self._ensure_connected():
            return None
        self._msg_id += 1
        msg = json.dumps({"id": self._msg_id, "method": method, "params": params or {}})
        try:
            self._ws.send(msg)
            return json.loads(self._ws.recv(timeout=10))
        except Exception as e:
            log_warn(f"CDP raw error ({method}): {e}")
            self._ws = None
            return None

    def close(self):
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass
            self._ws = None

    def _check_no_tokens(self) -> bool:
        """Verifica si ChatGPT muestra error de tokens de imagen agotados."""
        result = self.evaluate("""(() => {
            const text = (document.body?.innerText || '').toLowerCase()
                .normalize('NFD').replace(/[\\u0300-\\u036f]/g, '');
            const phrases = [
                'has alcanzado tu limite de creacion de imagenes',
                'el limite se restablece',
                'youve hit the team plan limit for image generations',
                'you can create more images when the limit resets',
                'no pude invocar la herramienta de generacion de imagenes',
                'cannot generate more images',
                'image generation limit',
            ];
            return phrases.some(p => text.includes(p)) ? 'YES' : 'NO';
        })()""")
        return result == "YES"

    # ── Acciones de ChatGPT ──────────────────────────────────────────────

    def wait_for_page_ready(self, timeout_sec: int = 60) -> bool:
        """Espera que ChatGPT cargue completamente (sin CAPTCHA)."""
        deadline = time.time() + timeout_sec
        consecutive_fails = 0
        while time.time() < deadline:
            title = self.evaluate("document.title") or ""

            # Si evaluate falla (timeout CDP), reconectar y reintentar
            if title is None or title == "":
                consecutive_fails += 1
                if consecutive_fails >= 2:
                    log_info("CDP no responde, reconectando...")
                    self._ws = None
                    time.sleep(3)
                    if not self.connect():
                        time.sleep(2)
                        continue
                    consecutive_fails = 0
                else:
                    time.sleep(2)
                continue

            consecutive_fails = 0

            if "un momento" in title.lower() or "just a moment" in title.lower():
                log_info("Esperando CAPTCHA de Cloudflare...")
                time.sleep(2)
                continue
            if title == "about:blank":
                time.sleep(1)
                continue
            # Verificar que el editor esté listo
            ready = self.evaluate("""(() => {
                const editor = document.querySelector('#prompt-textarea[contenteditable="true"]');
                return (editor && editor.getBoundingClientRect().width > 50) ? 'READY' : 'NOT_READY';
            })()""")
            if ready and "READY" in str(ready) and "NOT" not in str(ready):
                return True
            time.sleep(1)
        return False

    def paste_prompt(self, prompt: str) -> bool:
        """Pega el prompt en el editor de ChatGPT via CDP Input.insertText.
        Usa Selection/Range + Backspace para limpiar, luego inserta por chunks.
        No interpola texto en JS — elimina riesgo de inyección.
        """
        selector = '#prompt-textarea[contenteditable="true"]'

        # 1. Focus con Selection/Range
        self.evaluate(f"""(() => {{
            const editor = document.querySelector('{selector}');
            if (!editor) return;
            editor.focus();
            const selection = window.getSelection();
            const range = document.createRange();
            range.selectNodeContents(editor);
            range.collapse(false);
            selection.removeAllRanges();
            selection.addRange(range);
        }})()""")
        time.sleep(0.3)

        # 2. Clear con selectNodeContents + Backspace CDP
        self.evaluate(f"""(() => {{
            const editor = document.querySelector('{selector}');
            if (!editor) return;
            editor.focus();
            const selection = window.getSelection();
            const range = document.createRange();
            range.selectNodeContents(editor);
            selection.removeAllRanges();
            selection.addRange(range);
        }})()""")
        self._send_raw("Input.dispatchKeyEvent", {
            "type": "keyDown", "key": "Backspace", "code": "Backspace",
            "windowsVirtualKeyCode": 8, "nativeVirtualKeyCode": 8,
        })
        self._send_raw("Input.dispatchKeyEvent", {
            "type": "keyUp", "key": "Backspace", "code": "Backspace",
            "windowsVirtualKeyCode": 8, "nativeVirtualKeyCode": 8,
        })
        time.sleep(0.3)

        # 3. Focus de nuevo
        self.evaluate(f"""(() => {{
            const editor = document.querySelector('{selector}');
            if (!editor) return;
            editor.focus();
            const selection = window.getSelection();
            const range = document.createRange();
            range.selectNodeContents(editor);
            range.collapse(false);
            selection.removeAllRanges();
            selection.addRange(range);
        }})()""")
        time.sleep(0.3)

        # 4. Insert con Input.insertText por chunks (CDP directo, sin JS string)
        CHUNK_SIZE = 200
        for i in range(0, len(prompt), CHUNK_SIZE):
            chunk = prompt[i:i + CHUNK_SIZE]
            self._send_raw("Input.insertText", {"text": chunk})
            if i + CHUNK_SIZE < len(prompt):
                time.sleep(0.05)

        # 5. Verificar que se registró
        time.sleep(0.5)
        text = self.evaluate(f"""(() => {{
            const editor = document.querySelector('{selector}');
            return (editor?.innerText || '').trim().substring(0, 100);
        }})()""") or ""

        if len(text) >= min(len(prompt), 3):
            log_ok(f"Prompt pegado ({len(prompt)} chars)")
            return True

        log_warn(f"El prompt no se registró en el editor (esperado >= {min(len(prompt), 3)}, obtuvo {len(text)})")
        return False

    def send_prompt(self) -> bool:
        """Hace click en el botón de enviar."""
        result = self.evaluate("""(() => {
            // Buscar botón send
            const btn = document.querySelector('button[data-testid="send-button"]')
                || document.querySelector('button[aria-label="Send prompt"]')
                || document.querySelector('form button[type="submit"]');
            if (btn && !btn.disabled) {
                btn.click();
                return 'CLICKED';
            }
            return 'NO_BUTTON';
        })()""")

        if result and "CLICKED" in str(result):
            log_ok("Prompt enviado")
            return True

        # Fallback: Enter via CDP
        log_info("Botón no encontrado, intentando Enter...")
        self.evaluate("""(() => {
            const editor = document.querySelector('#prompt-textarea[contenteditable="true"]');
            if (editor) editor.focus();
        })()""")
        self._send_raw("Input.dispatchKeyEvent", {
            "type": "keyDown", "key": "Enter", "code": "Enter",
            "windowsVirtualKeyCode": 13,
        })
        self._send_raw("Input.dispatchKeyEvent", {
            "type": "keyUp", "key": "Enter", "code": "Enter",
            "windowsVirtualKeyCode": 13,
        })
        time.sleep(2)

        # Verificar que el editor se vació (prompt enviado)
        text_after = self.evaluate("""(() => {
            const editor = document.querySelector('#prompt-textarea[contenteditable="true"]');
            return (editor?.innerText || '').trim();
        })()""") or ""

        if len(text_after) < 5:
            log_ok("Prompt enviado via Enter")
            return True

        log_warn("Enter no envió el prompt")
        return False

    def wait_for_response(self, timeout_sec: int = 120) -> bool:
        """Espera que ChatGPT termine de generar la respuesta."""
        deadline = time.time() + timeout_sec
        time.sleep(3)  # Esperar a que empiece a generar

        while time.time() < deadline:
            # Verificar si el botón de stop está visible (generando)
            generating = self.evaluate("""(() => {
                const stop = document.querySelector('button[data-testid="stop-button"]')
                    || document.querySelector('button[aria-label="Stop generating"]');
                return stop ? 'GENERATING' : 'DONE';
            })()""")

            if generating != "GENERATING":
                log_ok("Respuesta completada")
                return True

            time.sleep(1)

        log_warn("Timeout esperando respuesta")
        return False

    def get_last_response(self) -> str:
        """Extrae la última respuesta de ChatGPT."""
        result = self.evaluate("""(() => {
            const messages = document.querySelectorAll('[data-message-author-role="assistant"]');
            if (!messages.length) return '';
            const last = messages[messages.length - 1];
            return (last.innerText || '').trim();
        })()""")
        return str(result or "")

    # ── Detección de tokens y sesión ─────────────────────────────────────

    def detect_token_status(self, timeout_sec: int = 90) -> str:
        """Espera la respuesta de ChatGPT y detecta si hay error de tokens o sesión.

        Retorna: 'success', 'no_image_tokens', 'session_expired'

        Flujo:
          1. Esperar a que ChatGPT empiece a responder (nuevo turno del asistente)
          2. Esperar a que termine de responder (stop button desaparece)
          3. Verificar si la respuesta es un error de tokens
        """
        deadline = time.time() + timeout_sec

        # ── Fase 1: Esperar que ChatGPT empiece a responder ──────────────
        # Contar turnos actuales para detectar un nuevo turno del asistente
        initial_turns = self.evaluate("""(() => {
            return document.querySelectorAll(
                '[data-testid^="conversation-turn"],[data-message-id],article'
            ).length;
        })()""")
        initial_count = int(initial_turns or "0")

        response_started = False
        while time.time() < deadline:
            current = self.evaluate("""(() => {
                return document.querySelectorAll(
                    '[data-testid^="conversation-turn"],[data-message-id],article'
                ).length;
            })()""")
            if int(current or "0") > initial_count:
                response_started = True
                break

            # Mientras esperamos, chequear tokens por si el mensaje ya apareció
            if self._check_no_tokens():
                return "no_image_tokens"

            time.sleep(1)

        if not response_started:
            # Timeout esperando respuesta — verificar tokens una vez más
            if self._check_no_tokens():
                return "no_image_tokens"
            return "success"

        # ── Fase 2: Esperar que ChatGPT termine de responder ─────────────
        idle_cycles = 0
        while time.time() < deadline:
            # Chequear tokens en cada ciclo (puede aparecer en cualquier momento)
            if self._check_no_tokens():
                return "no_image_tokens"

            if self._check_session_expired():
                return "session_expired"

            generating = self.evaluate("""(() => {
                const stop = document.querySelector('button[data-testid="stop-button"]')
                    || document.querySelector('button[aria-label="Stop generating"]');
                const progress = document.querySelector('[role="progressbar"]');
                const body = (document.body?.innerText || '').toLowerCase();
                const creating = body.includes('creando imagen') || body.includes('creating image');
                return (stop || progress || creating) ? 'YES' : 'NO';
            })()""")

            if generating == "YES":
                idle_cycles = 0
            else:
                idle_cycles += 1
                # Esperar suficientes ciclos para que el mensaje completo se renderice
                if idle_cycles >= 8:
                    break

            time.sleep(1)

        # ── Fase 3: Verificación final después de que ChatGPT terminó ────
        # Dar tiempo extra para que el DOM se actualice completamente
        time.sleep(2)
        if self._check_no_tokens():
            return "no_image_tokens"
        if self._check_session_expired():
            return "session_expired"

        return "success"

    def _check_no_tokens(self) -> bool:
        """Verifica si el texto de la página indica tokens de imagen agotados."""
        result = self.evaluate("""(() => {
            const text = (document.body?.innerText || '').toLowerCase()
                .normalize('NFD').replace(/[\\u0300-\\u036f]/g, '');
            const phrases = [
                'has alcanzado tu limite',
                'el limite se restablece',
                'hit the team plan limit',
                'hit the plan limit',
                'youve hit',
                'you can create more images when the limit resets',
                'no pude invocar la herramienta de generacion de imagenes',
                'cannot generate more images',
                'image generation limit',
                'image generations request',
                'limite de generacion de imagen',
                'limite de creacion de imagen',
                'the limit resets in',
            ];
            return phrases.some(p => text.includes(p)) ? 'YES' : 'NO';
        })()""")
        if result == "YES":
            log_warn("Tokens de imagen agotados detectados")
            return True
        return False

    def _check_session_expired(self) -> bool:
        """Verifica si la sesión de ChatGPT expiró."""
        result = self.evaluate("""(() => {
            const text = (document.body?.innerText || '').toLowerCase()
                .normalize('NFD').replace(/[\\u0300-\\u036f]/g, '');
            const phrases = [
                'tu sesion ha caducado',
                'vuelve a iniciar sesion',
                'your session has expired',
                'sign in again to continue',
                'session expired',
            ];
            const hasDialog = !!document.querySelector('[role="dialog"]');
            return (hasDialog && phrases.some(p => text.includes(p))) ? 'YES' : 'NO';
        })()""")
        if result == "YES":
            log_warn("Sesión expirada detectada")
            return True
        return False

    # ── Rotación de cuentas ──────────────────────────────────────────────

    def switch_account(self, exhausted_ids: set[str]) -> dict:
        """Abre menú de perfil, lista cuentas, click en una no agotada.
        Retorna dict con switched, account_id, account_label, available_count.
        """
        # 1. Click en botón de perfil (múltiples selectores por si la UI cambió)
        clicked = self.evaluate("""(() => {
            const selectors = [
                '[data-testid="accounts-profile-button"]',
                '[data-testid="profile-button"]',
                'button[aria-label*="Account"]',
                'button[aria-label*="Cuenta"]',
                'button[aria-label*="Profile"]',
                'button[aria-label*="Perfil"]',
                'nav button img[alt]',
            ];
            for (const sel of selectors) {
                const btn = document.querySelector(sel);
                if (btn) {
                    const target = btn.closest('button') || btn;
                    target.click();
                    return 'CLICKED:' + sel;
                }
            }
            return 'NO_BUTTON';
        })()""")
        if not clicked or not clicked.startswith("CLICKED"):
            log_warn("No se encontró botón de perfil")
            return {"switched": False, "reason": "no_profile_button"}
        log_info(f"Botón de perfil clickeado: {clicked}")

        time.sleep(2)

        # 2. Esperar menú (múltiples intentos)
        menu_found = False
        for _try in range(3):
            menu_ready = self.evaluate("""(() => {
                const menu = document.querySelector('[role="menu"]')
                    || document.querySelector('[data-radix-menu-content]')
                    || document.querySelector('[role="listbox"]');
                return menu ? 'YES' : 'NO';
            })()""")
            if menu_ready == "YES":
                menu_found = True
                break
            time.sleep(1)

        if not menu_found:
            log_warn("Menú de perfil no apareció")
            return {"switched": False, "reason": "menu_not_found"}

        # 3. Listar cuentas
        exhausted_json = json.dumps(list(exhausted_ids))
        accounts_raw = self.evaluate(f"""(() => {{
            const exhausted = {exhausted_json};
            const items = Array.from(document.querySelectorAll('[role="menuitemradio"]'));
            const profileBtn = document.querySelector('[data-testid="accounts-profile-button"]');
            const currentText = (profileBtn?.innerText || '').trim().toLowerCase();

            const accounts = items.map((el, i) => {{
                const text = (el.innerText || '').trim();
                const normalized = text.toLowerCase().normalize('NFD').replace(/[\\u0300-\\u036f]/g, '');
                const accountId = 'slot:' + i + '|label:' + (normalized || 'sin_texto');
                const ariaChecked = el.getAttribute('aria-checked') === 'true';
                const hasCheckmark = !!el.querySelector('.trailing svg.icon-sm');
                const looksCurrent = ariaChecked || hasCheckmark
                    || currentText.includes(normalized.substring(0, 10));
                const excluded = exhausted.includes(accountId);
                return {{index: i, accountId, label: text, looksCurrent, excluded}};
            }});

            const available = accounts.filter(a => !a.looksCurrent);
            const candidates = available.filter(a => !a.excluded);
            return JSON.stringify({{accounts, available: available.length, candidates}});
        }})()""")

        if not accounts_raw:
            return {"switched": False, "reason": "no_accounts_data"}

        try:
            data = json.loads(accounts_raw)
        except Exception:
            return {"switched": False, "reason": "parse_error"}

        candidates = data.get("candidates", [])
        available_count = data.get("available", 0)

        if not candidates:
            log_warn(f"Sin cuentas disponibles (total disponibles: {available_count})")
            return {"switched": False, "available_count": available_count, "reason": "no_candidates"}

        # 4. Click en primera candidata
        chosen = candidates[0]
        chosen_idx = chosen["index"]
        log_info(f"Cambiando a cuenta: {chosen['label']} (index={chosen_idx})")

        self.evaluate(f"""(() => {{
            const items = document.querySelectorAll('[role="menuitemradio"]');
            if (items[{chosen_idx}]) items[{chosen_idx}].click();
        }})()""")

        # 5. Esperar recarga (cambio de cuenta causa navegación)
        time.sleep(5)

        # 6. Reconectar WebSocket
        self._ws = None
        self.connect()

        # 7. Navegar a chat limpio
        self.evaluate("window.location.href = 'https://chatgpt.com/'")
        time.sleep(4)
        self._ws = None
        self.connect()

        # 8. Esperar editor ready
        self.wait_for_page_ready(timeout_sec=15)

        log_ok(f"Cuenta cambiada a: {chosen['label']}")
        return {
            "switched": True,
            "account_id": chosen["accountId"],
            "account_label": chosen["label"],
            "available_count": available_count,
            "reason": "account_switched",
        }


# ── Función principal ────────────────────────────────────────────────────────

def paste_and_send_prompt(port: int, prompt: str, wait_response: bool = True, timeout: int = 120) -> dict:
    """
    Pega y envía un prompt en ChatGPT via CDP.

    Args:
        port: Puerto CDP del navegador con ChatGPT abierto
        prompt: Texto del prompt a enviar
        wait_response: Si True, espera la respuesta completa
        timeout: Timeout en segundos para la respuesta

    Returns:
        dict con status, response (si wait_response=True), y detalles
    """
    session = ChatGPTSession(port=port)

    if not session.connect():
        return {"success": False, "error": f"No se pudo conectar a ChatGPT en puerto {port}"}

    try:
        # Esperar que la página esté lista
        if not session.wait_for_page_ready(timeout_sec=30):
            return {"success": False, "error": "ChatGPT no cargó completamente"}

        # Pegar prompt
        if not session.paste_prompt(prompt):
            return {"success": False, "error": "No se pudo pegar el prompt"}

        # Enviar
        if not session.send_prompt():
            return {"success": False, "error": "No se pudo enviar el prompt"}

        result = {
            "success": True,
            "port": port,
            "prompt_length": len(prompt),
            "sent": True,
        }

        # Esperar respuesta si se pidió
        if wait_response:
            if session.wait_for_response(timeout_sec=timeout):
                result["response"] = session.get_last_response()
                result["response_complete"] = True
            else:
                result["response"] = session.get_last_response()
                result["response_complete"] = False

        return result

    finally:
        session.close()


MAX_ROTATION_ATTEMPTS = 20


def paste_and_send_with_rotation(
    port: int, prompt: str, wait_response: bool = True, timeout: int = 120,
) -> dict:
    """
    Pega y envía prompt con rotación automática de cuentas.

    Si ChatGPT responde 'sin tokens de imagen', cambia a otra cuenta
    dentro de ChatGPT y reintenta. Hasta 20 intentos.

    Si 'sesión expirada', retorna error para que el caller cambie perfil DiCloak.
    """
    session = ChatGPTSession(port=port)
    if not session.connect():
        return {"success": False, "error": f"No se pudo conectar a ChatGPT en puerto {port}"}

    last_account_id = ""
    last_account_label = ""
    rotations = 0

    try:
        for attempt in range(MAX_ROTATION_ATTEMPTS):
            log_info(f"Intento {attempt + 1}/{MAX_ROTATION_ATTEMPTS}")

            # Esperar página lista (60s para dar tiempo a que cargue)
            if not session.wait_for_page_ready(timeout_sec=60):
                return {"success": False, "error": "ChatGPT no cargó completamente"}

            # Pegar prompt
            if not session.paste_prompt(prompt):
                return {"success": False, "error": "No se pudo pegar el prompt"}

            # Enviar
            if not session.send_prompt():
                return {"success": False, "error": "No se pudo enviar el prompt"}

            # Detectar estado de tokens
            status = session.detect_token_status(timeout_sec=timeout)

            if status == "success":
                # Limpiar cuenta previa si funcionó
                if last_account_id:
                    clear_exhausted(port, last_account_id)

                result = {
                    "success": True,
                    "port": port,
                    "prompt_length": len(prompt),
                    "sent": True,
                    "rotations": rotations,
                }
                if wait_response:
                    result["response"] = session.get_last_response()
                    result["response_complete"] = True
                return result

            if status == "session_expired":
                return {
                    "success": False,
                    "error": "session_expired",
                    "message": "La sesión de ChatGPT expiró. Cambiar perfil DiCloak.",
                    "rotations": rotations,
                }

            # no_image_tokens → marcar cuenta y rotar
            if last_account_id:
                mark_exhausted(port, last_account_id, last_account_label)
                log_info(f"Cuenta agotada marcada: {last_account_label or last_account_id}")

            log_info("Tokens agotados. Rotando cuenta...")
            exhausted = get_exhausted_ids(port)
            switch_result = session.switch_account(exhausted)

            if not switch_result.get("switched"):
                reason = switch_result.get("reason", "unknown")
                return {
                    "success": False,
                    "error": "all_accounts_exhausted",
                    "message": f"Sin cuentas disponibles: {reason}",
                    "rotations": rotations,
                }

            last_account_id = switch_result.get("account_id", "")
            last_account_label = switch_result.get("account_label", "")
            rotations += 1
            log_ok(f"Rotación #{rotations}: {last_account_label}")
            time.sleep(2)

        return {
            "success": False,
            "error": "max_rotation_attempts",
            "message": f"Se agotaron los {MAX_ROTATION_ATTEMPTS} intentos de rotación",
            "rotations": rotations,
        }

    finally:
        session.close()
