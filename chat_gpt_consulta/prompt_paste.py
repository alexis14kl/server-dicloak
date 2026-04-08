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

_MIN_SEND_INTERVAL_SEC = max(0, int(os.environ.get("CHATGPT_MIN_SEND_INTERVAL_SEC", "12")))
_RATE_LIMIT_COOLDOWN_SEC = max(
    _MIN_SEND_INTERVAL_SEC,
    int(os.environ.get("CHATGPT_RATE_LIMIT_COOLDOWN_SEC", "180")),
)
_LAST_SEND_BY_PORT: dict[int, float] = {}
_IMAGE_ANCHORS_BY_WS: dict[str, dict] = {}


def _capture_image_generation_anchor(session: "ChatGPTSession") -> dict:
    """Captura el estado base del chat antes de generar una nueva imagen.

    Sirve para ignorar imágenes previas cuando reutilizamos la misma conversación.
    """
    result = session.evaluate("""(() => {
        const turns = Array.from(document.querySelectorAll(
            '[data-testid^="conversation-turn"][data-turn="assistant"], section[data-turn="assistant"]'
        ));
        const turnIds = turns.map((turn) =>
            turn.getAttribute('data-turn-id')
            || turn.getAttribute('data-testid')
            || ''
        ).filter(Boolean);
        const imageUrls = Array.from(turns.flatMap((turn) =>
            Array.from(turn.querySelectorAll('img')).map((img) => img.currentSrc || img.src || '')
        )).filter((url) => {
            if (!url) return false;
            return url.includes('/backend-api/')
                || url.includes('oaidalleapi')
                || url.includes('openai.com')
                || url.includes('chatgpt.com')
                || url.includes('blob:');
        });
        return JSON.stringify({
            captured_at: Date.now(),
            known_turn_ids: turnIds,
            known_image_urls: Array.from(new Set(imageUrls)),
        });
    })()""")

    if not result:
        return {"known_turn_ids": [], "known_image_urls": []}
    try:
        data = json.loads(result)
        if isinstance(data, dict):
            data.setdefault("known_turn_ids", [])
            data.setdefault("known_image_urls", [])
            return data
    except Exception:
        pass
    return {"known_turn_ids": [], "known_image_urls": []}


def _remember_image_generation_anchor(ws_url: str, anchor: dict) -> None:
    if not ws_url:
        return
    _IMAGE_ANCHORS_BY_WS[ws_url] = anchor


def get_image_generation_anchor(ws_url: str) -> dict:
    if not ws_url:
        return {}
    return dict(_IMAGE_ANCHORS_BY_WS.get(ws_url, {}))


@dataclass
class ChatGPTSession:
    """Sesión CDP activa con una instancia de ChatGPT."""
    port: int
    ws_url: str = ""
    _ws: object = field(default=None, repr=False)
    _msg_id: int = field(default=0, repr=False)
    last_error: str = field(default="", repr=False)

    def connect(self) -> bool:
        """Conecta al CDP del navegador de ChatGPT.
        Si ws_url ya está seteado (target_ws), reconecta a esa misma tab.
        """
        try:
            import websockets.sync.client as ws_sync
        except ImportError:
            import subprocess
            subprocess.check_call([sys.executable, "-m", "pip", "install", "websockets", "-q"])
            import websockets.sync.client as ws_sync

        # Si ya tenemos ws_url (target_ws), reconectar a la misma tab
        if not self.ws_url:
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
        """Busca la página de ChatGPT entre los targets CDP.
        Retorna la ULTIMA tab de ChatGPT (la mas reciente), no la primera.
        """
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json", timeout=3) as resp:
                targets = json.loads(resp.read().decode("utf-8"))
            last_ws = ""
            for t in targets:
                url = (t.get("url") or "").lower()
                if "chatgpt.com" in url and t.get("type") == "page":
                    last_ws = t.get("webSocketDebuggerUrl", "")
            if last_ws:
                return last_ws
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

    def _check_rate_limited(self) -> bool:
        """Verifica si ChatGPT muestra el modal de 'Demasiadas solicitudes'."""
        result = self.evaluate("""(() => {
            const text = (document.body?.innerText || '').toLowerCase()
                .normalize('NFD').replace(/[\\u0300-\\u036f]/g, '');
            const phrases = [
                'demasiadas solicitudes',
                'too many requests',
                'estas haciendo solicitudes demasiado rapido',
                'youre sending messages too quickly',
                'rate limit',
                'limitado temporalmente',
                'temporarily limited',
            ];
            return phrases.some(p => text.includes(p)) ? 'YES' : 'NO';
        })()""")
        return result == "YES"

    def _dismiss_rate_limit_modal(self) -> None:
        """Cierra el modal de rate limit si está visible."""
        self.evaluate("""(() => {
            const btns = Array.from(document.querySelectorAll('button'));
            const ok = btns.find(b => /entendido|understood|ok|got it/i.test(b.innerText));
            if (ok) ok.click();
        })()""")

    def _mark_rate_limited(self) -> None:
        self.last_error = "rate_limited"

    def _respect_send_pacing(self) -> None:
        """Impone una pausa mínima entre envíos por perfil/CDP."""
        if _MIN_SEND_INTERVAL_SEC <= 0:
            return
        now = time.time()
        last_send = _LAST_SEND_BY_PORT.get(self.port, 0.0)
        remaining = (last_send + _MIN_SEND_INTERVAL_SEC) - now
        if remaining > 0:
            wait_for = round(remaining, 1)
            log_info(f"Pacing anti-rate-limit: esperando {wait_for}s antes de enviar...")
            time.sleep(remaining)

    # ── Acciones de ChatGPT ──────────────────────────────────────────────

    def wait_for_page_ready(self, timeout_sec: int = 60) -> bool:
        """Espera que ChatGPT cargue completamente (sin CAPTCHA)."""
        self.last_error = ""
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
                time.sleep(3)
                continue
            if title == "about:blank":
                time.sleep(2)
                continue

            # Un solo evaluate: editor ready + rate limit check (reduce CDP calls)
            status = self.evaluate("""(() => {
                const text = (document.body?.innerText || '').toLowerCase();
                if (text.includes('demasiadas solicitudes') || text.includes('too many requests')
                    || text.includes('rate limit') || text.includes('limitado temporalmente'))
                    return 'RATE_LIMITED';
                const editor = document.querySelector('#prompt-textarea[contenteditable="true"]');
                return (editor && editor.getBoundingClientRect().width > 50) ? 'READY' : 'NOT_READY';
            })()""")

            if status == "RATE_LIMITED":
                self._mark_rate_limited()
                self._dismiss_rate_limit_modal()
                log_warn("Rate limit detectado en page_ready — abortando")
                return False
            if status == "READY":
                return True
            time.sleep(2)
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
        self.last_error = ""

        if self._check_rate_limited():
            self._mark_rate_limited()
            self._dismiss_rate_limit_modal()
            log_warn("Rate limit detectado antes de enviar")
            return False

        self._respect_send_pacing()

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

        if result and "CLICKED" not in str(result):
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

        if self._check_rate_limited():
            self._mark_rate_limited()
            self._dismiss_rate_limit_modal()
            log_warn("Rate limit detectado justo después de enviar")
            return False

        # Verificar que el editor se vació (prompt enviado)
        text_after = self.evaluate("""(() => {
            const editor = document.querySelector('#prompt-textarea[contenteditable="true"]');
            return (editor?.innerText || '').trim();
        })()""") or ""

        if len(text_after) < 5:
            _LAST_SEND_BY_PORT[self.port] = time.time()
            log_ok("Prompt enviado via Enter")
            return True

        log_warn("Enter no envió el prompt")
        return False

    def wait_for_response(self, timeout_sec: int = 120) -> bool:
        """Espera que ChatGPT termine de generar — sin polling, usa MutationObserver."""
        timeout_ms = timeout_sec * 1000

        result = self.evaluate(f"""
        new Promise((resolve) => {{
            function isGenerating() {{
                return !!(
                    document.querySelector('button[data-testid="stop-button"]')
                    || document.querySelector('button[aria-label="Stop generating"]')
                    || document.querySelector('button[aria-label="Detener la generación"]')
                );
            }}

            // Si ya terminó
            if (!isGenerating()) {{ resolve('DONE'); return; }}

            let observer = null;
            let timer = null;

            function cleanup() {{
                if (observer) observer.disconnect();
                if (timer) clearTimeout(timer);
            }}

            observer = new MutationObserver(() => {{
                if (!isGenerating()) {{
                    cleanup();
                    resolve('DONE');
                }}
            }});

            observer.observe(document.body, {{
                childList: true,
                subtree: true,
                attributes: true,
                attributeFilter: ['data-testid', 'aria-label'],
            }});

            timer = setTimeout(() => {{
                cleanup();
                resolve(isGenerating() ? 'TIMEOUT' : 'DONE');
            }}, {timeout_ms});
        }})
        """, timeout=timeout_sec + 10, await_promise=True)

        status = str(result or "DONE").strip()
        if status == "DONE":
            log_ok("Respuesta completada")
            return True

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
        """Espera la respuesta de ChatGPT sin polling — usa MutationObserver.

        Inyecta UN solo evaluate con awaitPromise=True que resuelve cuando
        ChatGPT termina de generar. Frases de detección mergeadas de ambas versiones.

        Retorna: 'success', 'no_image_tokens', 'session_expired', 'rate_limited'
        """
        timeout_ms = timeout_sec * 1000

        result = self.evaluate(f"""
        new Promise((resolve) => {{
            const TIMEOUT = {timeout_ms};
            const CHECK_INTERVAL = 5000;

            const ratePhrases = [
                'demasiadas solicitudes', 'too many requests', 'rate limit',
                'limitado temporalmente', 'temporarily limited',
                "you've been temporarily restricted", "has been temporarily restricted",
                'te han restringido temporalmente', 'unusual activity',
            ];
            const tokenPhrases = [
                'has alcanzado tu limite', 'el limite se restablece',
                'hit the team plan limit', 'hit the plan limit', 'youve hit',
                'no pude invocar la herramienta de generacion de imagenes',
                'cannot generate more images', 'image generation limit',
                'limite de creacion de imagen', 'the limit resets in',
                "you've reached your limit", "upgrade your plan",
                "can't generate images", 'no puedo generar imagenes',
            ];
            const sessionPhrases = [
                'tu sesion ha caducado', 'vuelve a iniciar sesion',
                'your session has expired', 'please log in', 'inicia sesion',
                'log in to chatgpt', 'sign in to chatgpt',
            ];

            function checkText() {{
                const text = (document.body?.innerText || '').toLowerCase()
                    .normalize('NFD').replace(/[\\u0300-\\u036f]/g, '');
                if (ratePhrases.some(p => text.includes(p))) return 'RATE_LIMITED';
                if (tokenPhrases.some(p => text.includes(p))) return 'NO_TOKENS';
                if (sessionPhrases.some(p => text.includes(p))) return 'SESSION_EXPIRED';
                return null;
            }}

            function isGenerating() {{
                return !!(
                    document.querySelector('button[data-testid="stop-button"]')
                    || document.querySelector('button[aria-label="Stop generating"]')
                    || document.querySelector('button[aria-label="Detener la generación"]')
                    || document.querySelector('[role="progressbar"]')
                );
            }}

            let idleCount = 0;
            let textCheckTimer = null;
            let timeoutTimer = null;
            let observer = null;

            function cleanup() {{
                if (observer) observer.disconnect();
                if (textCheckTimer) clearInterval(textCheckTimer);
                if (timeoutTimer) clearTimeout(timeoutTimer);
            }}

            function done(status) {{
                cleanup();
                resolve(status);
            }}

            const immediate = checkText();
            if (immediate) {{ done(immediate); return; }}

            textCheckTimer = setInterval(() => {{
                const status = checkText();
                if (status) {{ done(status); return; }}
                if (!isGenerating()) {{
                    idleCount++;
                    if (idleCount >= 3) {{ done('SUCCESS'); }}
                }} else {{
                    idleCount = 0;
                }}
            }}, CHECK_INTERVAL);

            observer = new MutationObserver(() => {{
                if (!isGenerating()) {{
                    setTimeout(() => {{
                        const status = checkText();
                        done(status || 'SUCCESS');
                    }}, 2000);
                }}
            }});

            observer.observe(document.body, {{
                childList: true, subtree: true, attributes: true,
                attributeFilter: ['data-testid', 'aria-label', 'role'],
            }});

            timeoutTimer = setTimeout(() => {{
                const status = checkText();
                done(status || 'SUCCESS');
            }}, TIMEOUT);
        }})
        """, timeout=timeout_sec + 10, await_promise=True)

        status = str(result or "SUCCESS").strip()
        log_info(f"detect_token_status resultado: {status}")

        if status == "RATE_LIMITED":
            self._mark_rate_limited()
            self._dismiss_rate_limit_modal()
            log_warn("Rate limit detectado durante generación")
            return "rate_limited"

        if status == "NO_TOKENS":
            log_warn("Tokens de imagen agotados detectados")
            return "no_image_tokens"

        if status == "SESSION_EXPIRED":
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

    def _cdp_click_at(self, x: float, y: float):
        """Click real via CDP Input.dispatchMouseEvent — secuencia completa."""
        # mouseMoved primero (hover) para que React registre el target
        self._send_raw("Input.dispatchMouseEvent", {
            "type": "mouseMoved", "x": x, "y": y,
        })
        time.sleep(0.05)
        for event_type in ("mousePressed", "mouseReleased"):
            self._send_raw("Input.dispatchMouseEvent", {
                "type": event_type,
                "x": x, "y": y,
                "button": "left",
                "clickCount": 1,
            })
            time.sleep(0.05)

    def _js_full_click(self, selector: str) -> str | None:
        """Click via JS con secuencia completa de PointerEvent + MouseEvent.
        Radix UI (ChatGPT) necesita PointerEvents para abrir menús.
        """
        return self.evaluate(f"""(() => {{
            const selectors = {json.dumps([selector] if selector else [
                '[data-testid="accounts-profile-button"]',
                '[data-testid="profile-button"]',
                'button[aria-label*="Account"]',
                'button[aria-label*="Cuenta"]',
            ])};
            for (const sel of selectors) {{
                const el = document.querySelector(sel);
                if (!el) continue;
                const target = el.closest('button') || el;
                const rect = target.getBoundingClientRect();
                const opts = {{bubbles: true, cancelable: true, clientX: rect.x + rect.width/2, clientY: rect.y + rect.height/2}};
                target.dispatchEvent(new PointerEvent('pointerdown', {{...opts, pointerId: 1, pointerType: 'mouse'}}));
                target.dispatchEvent(new MouseEvent('mousedown', opts));
                target.dispatchEvent(new PointerEvent('pointerup', {{...opts, pointerId: 1, pointerType: 'mouse'}}));
                target.dispatchEvent(new MouseEvent('mouseup', opts));
                target.dispatchEvent(new MouseEvent('click', opts));
                return 'CLICKED:' + sel;
            }}
            return 'NO_BUTTON';
        }})()""")

    def get_current_account_id(self) -> str:
        """Lee el ID de la cuenta/workspace activa sin abrir el menú.

        Usa el texto visible del botón de perfil como identificador estable.
        Retorna string vacío si no puede determinarlo.
        """
        result = self.evaluate("""(() => {
            // Patrones que indican aria-label de UI, no nombre de cuenta
            const SKIP_PATTERNS = [
                'menu de perfil', 'profile menu', 'open profile menu',
                'abrir el menu', 'account menu', 'accounts menu',
            ];
            const selectors = [
                '[data-testid="accounts-profile-button"]',
                '[data-testid="profile-button"]',
                'button[aria-label*="Account"]',
                'button[aria-label*="Cuenta"]',
                'nav button img[alt]',
            ];
            for (const sel of selectors) {
                const btn = document.querySelector(sel);
                if (!btn) continue;
                // Preferir innerText (nombre visible de workspace) sobre aria-label (label de UI)
                const raw = (btn.innerText || '').trim() || (btn.getAttribute('aria-label') || '').trim();
                if (!raw) continue;
                const normalized = raw.toLowerCase()
                    .normalize('NFD')
                    .replace(/[\\u0300-\\u036f]/g, '')
                    .replace(/\\s+/g, ' ')
                    .trim();
                // Saltar si es un label de UI genérico, no un nombre de cuenta
                if (SKIP_PATTERNS.some(p => normalized.includes(p))) continue;
                if (normalized.length < 2) continue;
                return 'label:' + normalized;
            }
            return '';
        })()""") or ""
        return str(result).strip()

    def switch_account(self, exhausted_ids: set[str]) -> dict:
        """Abre menú de perfil, lista cuentas, click en una no agotada.
        Retorna dict con switched, account_id, account_label, available_count.
        """
        # 1. Encontrar botón de perfil
        btn_info = self.evaluate("""(() => {
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
                    const rect = target.getBoundingClientRect();
                    if (rect.width > 0 && rect.height > 0) {
                        return JSON.stringify({
                            x: rect.x + rect.width / 2,
                            y: rect.y + rect.height / 2,
                            sel: sel,
                        });
                    }
                }
            }
            return 'NO_BUTTON';
        })()""")

        if not btn_info or btn_info == "NO_BUTTON":
            log_warn("No se encontró botón de perfil")
            return {"switched": False, "reason": "no_profile_button"}

        try:
            pos = json.loads(btn_info)
        except Exception:
            log_warn(f"Error parseando posición del botón: {btn_info}")
            return {"switched": False, "reason": "btn_parse_error"}

        sel = pos.get("sel", "")
        log_info(f"Botón de perfil encontrado: {sel} en ({pos['x']:.0f}, {pos['y']:.0f})")

        # 2. Intentar abrir menú con 3 estrategias distintas
        menu_found = False
        strategies = [
            ("PointerEvent JS (Radix)", lambda: self._js_full_click(sel)),
            ("CDP dispatchMouseEvent", lambda: self._cdp_click_at(pos["x"], pos["y"])),
            ("focus + Enter key", lambda: self.evaluate(f"""(() => {{
                const btn = document.querySelector('{sel}');
                if (btn) {{ (btn.closest('button') || btn).focus(); }}
            }})()""") or self._send_raw("Input.dispatchKeyEvent", {
                "type": "keyDown", "key": "Enter", "code": "Enter",
                "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13,
            })),
        ]

        for name, action in strategies:
            log_info(f"Intentando abrir menú: {name}")
            action()
            time.sleep(1.5)

            # Verificar si el menú apareció
            for _check in range(5):
                menu_ready = self.evaluate("""(() => {
                    const menu = document.querySelector('[role="menu"]')
                        || document.querySelector('[data-radix-menu-content]')
                        || document.querySelector('[role="listbox"]');
                    return menu ? 'YES' : 'NO';
                })()""")
                if menu_ready == "YES":
                    menu_found = True
                    break
                time.sleep(0.7)

            if menu_found:
                log_ok(f"Menú abierto con: {name}")
                break

        if not menu_found:
            log_warn("Menú de perfil no apareció con ninguna estrategia")
            return {"switched": False, "reason": "menu_not_found"}

        # 3. Abrir el submenú de cuentas/empresa si aún no aparecen las cuentas.
        radios_ready = self.evaluate("""(() => {
            return document.querySelectorAll('[role="menuitemradio"]').length > 0 ? 'YES' : 'NO';
        })()""")
        if radios_ready != "YES":
            # Encontrar el elemento de submenú
            submenu_sel_js = """(() => {
                const normalize = (value) => (value || '')
                    .toLowerCase()
                    .normalize('NFD')
                    .replace(/[\u0300-\u036f]/g, '');
                const rows = Array.from(document.querySelectorAll(
                    '[role="menuitem"][data-has-submenu], [role="menuitem"][aria-haspopup="menu"]'
                ));
                const target = rows.find((el) => {
                    const text = normalize(el.innerText || '');
                    if (!text || text.includes('ayuda')) return false;
                    return text.includes('empresa') || text.includes('enterprise') || text.includes('chatgpt pro');
                }) || null;
                if (!target) return 'NO_SUBMENU';
                const rect = target.getBoundingClientRect();
                return JSON.stringify({
                    text: (target.innerText || '').trim(),
                    x: rect.x + rect.width / 2,
                    y: rect.y + rect.height / 2,
                });
            })()"""

            submenu_pos_raw = self.evaluate(submenu_sel_js)
            if not submenu_pos_raw or submenu_pos_raw == "NO_SUBMENU":
                log_warn("No se encontró el submenú de cuentas/empresa")
                return {"switched": False, "reason": "accounts_submenu_missing"}

            try:
                submenu_pos = json.loads(submenu_pos_raw)
            except Exception:
                return {"switched": False, "reason": "submenu_parse_error"}

            log_info(f"Abriendo submenú: {submenu_pos.get('text', '')} en ({submenu_pos['x']:.0f}, {submenu_pos['y']:.0f})")

            # Intentar abrir el submenú con múltiples estrategias (Radix es sensible al hover)
            submenu_strategies = [
                ("mousemove + click CDP", lambda: (
                    self._send_raw("Input.dispatchMouseEvent", {
                        "type": "mouseMoved", "x": submenu_pos["x"], "y": submenu_pos["y"],
                        "button": "none", "buttons": 0,
                    }) or
                    time.sleep(0.3) or
                    self._cdp_click_at(submenu_pos["x"], submenu_pos["y"])
                )),
                ("PointerEvent JS (Radix)", lambda: self.evaluate(f"""(() => {{
                    const items = Array.from(document.querySelectorAll(
                        '[role="menuitem"][data-has-submenu], [role="menuitem"][aria-haspopup="menu"]'
                    ));
                    const el = items.find(e => e.getBoundingClientRect().x > 0);
                    if (!el) return;
                    const r = el.getBoundingClientRect();
                    const opts = {{bubbles:true,cancelable:true,composed:true,clientX:r.x+r.width/2,clientY:r.y+r.height/2}};
                    el.dispatchEvent(new PointerEvent('pointerenter', {{...opts,pointerId:1,pointerType:'mouse'}}));
                    el.dispatchEvent(new PointerEvent('pointermove', {{...opts,pointerId:1,pointerType:'mouse'}}));
                    el.dispatchEvent(new MouseEvent('mouseover', opts));
                    el.dispatchEvent(new MouseEvent('mouseenter', opts));
                    el.dispatchEvent(new PointerEvent('pointerdown', {{...opts,pointerId:1,pointerType:'mouse'}}));
                    el.dispatchEvent(new MouseEvent('mousedown', opts));
                    el.dispatchEvent(new PointerEvent('pointerup', {{...opts,pointerId:1,pointerType:'mouse'}}));
                    el.dispatchEvent(new MouseEvent('mouseup', opts));
                    el.dispatchEvent(new MouseEvent('click', opts));
                }})()""")),
                ("focus + ArrowRight", lambda: self.evaluate("""(() => {
                    const items = Array.from(document.querySelectorAll(
                        '[role="menuitem"][data-has-submenu], [role="menuitem"][aria-haspopup="menu"]'
                    ));
                    const el = items.find(e => e.getBoundingClientRect().x > 0);
                    if (el) el.focus();
                })()""") or self._send_raw("Input.dispatchKeyEvent", {
                    "type": "keyDown", "key": "ArrowRight", "code": "ArrowRight",
                    "windowsVirtualKeyCode": 39, "nativeVirtualKeyCode": 39,
                })),
            ]

            for strat_name, strat_action in submenu_strategies:
                log_info(f"Intentando abrir submenú: {strat_name}")
                strat_action()
                for _check in range(8):
                    radios_ready = self.evaluate("""(() => {
                        return document.querySelectorAll('[role="menuitemradio"]').length > 0 ? 'YES' : 'NO';
                    })()""")
                    if radios_ready == "YES":
                        break
                    time.sleep(0.5)
                if radios_ready == "YES":
                    log_ok(f"Submenú abierto con: {strat_name}")
                    break

            if radios_ready != "YES":
                log_warn("El submenú abrió pero no aparecieron las cuentas")
                return {"switched": False, "reason": "accounts_submenu_not_opened"}

        # 4. Listar cuentas
        exhausted_json = json.dumps(list(exhausted_ids))
        accounts_raw = self.evaluate(f"""(() => {{
            const exhausted = {exhausted_json};
            const items = Array.from(document.querySelectorAll('[role="menuitemradio"]'));
            const profileBtn = document.querySelector('[data-testid="accounts-profile-button"]');
            const currentText = (profileBtn?.innerText || '').trim().toLowerCase();

            const accounts = items.map((el, i) => {{
                const text = (el.innerText || '').trim();
                const normalized = text.toLowerCase().normalize('NFD').replace(/[\\u0300-\\u036f]/g, '');
                // Usar label normalizado como ID estable (no el slot/índice que puede cambiar de orden)
                const accountId = 'label:' + (normalized || 'slot_' + i);
                const ariaChecked = el.getAttribute('aria-checked') === 'true';
                const dataChecked = el.getAttribute('data-state') === 'checked';
                const hasCheckmark = !!el.querySelector('svg.icon-sm') || !!el.querySelector('.trailing svg');
                return {{index: i, accountId, label: text, normalized, ariaChecked, dataChecked, hasCheckmark}};
            }});

            // Detectar cuenta actual: primero por señales fuertes (aria/data/checkmark).
            // Solo usar text match si NINGUNA cuenta tiene señal fuerte.
            const hasStrongSignal = accounts.some(a => a.ariaChecked || a.dataChecked || a.hasCheckmark);

            for (const a of accounts) {{
                if (hasStrongSignal) {{
                    a.looksCurrent = a.ariaChecked || a.dataChecked || a.hasCheckmark;
                }} else {{
                    // Fallback: text match solo si no hay señales fuertes
                    a.looksCurrent = a.normalized.length > 3 && currentText.includes(a.normalized);
                }}
                a.excluded = exhausted.includes(a.accountId);
            }}

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

        # 5. Click en primera candidata via CDP (evento real)
        chosen = candidates[0]
        chosen_idx = chosen["index"]
        log_info(f"Cambiando a cuenta: {chosen['label']} (index={chosen_idx})")

        # Click en cuenta via PointerEvent (Radix) — causa navegación inmediata.
        # El WebSocket se rompe — eso es ESPERADO, no es un error.
        self.evaluate(f"""(() => {{
            const items = document.querySelectorAll('[role="menuitemradio"]');
            const el = items[{chosen_idx}];
            if (!el) return;
            const rect = el.getBoundingClientRect();
            const opts = {{bubbles: true, cancelable: true, clientX: rect.x + rect.width/2, clientY: rect.y + rect.height/2}};
            el.dispatchEvent(new PointerEvent('pointerdown', {{...opts, pointerId: 1, pointerType: 'mouse'}}));
            el.dispatchEvent(new MouseEvent('mousedown', opts));
            el.dispatchEvent(new PointerEvent('pointerup', {{...opts, pointerId: 1, pointerType: 'mouse'}}));
            el.dispatchEvent(new MouseEvent('mouseup', opts));
            el.dispatchEvent(new MouseEvent('click', opts));
        }})()""")

        # 6. Esperar a que la página termine de navegar
        # (el click en cuenta causa navegación automática a ?account_switch=true)
        time.sleep(6)

        # 7. Reconectar: LIMPIAR ws_url para forzar búsqueda fresca del target
        self._ws = None
        self.ws_url = ""
        for _retry in range(5):
            if self.connect():
                break
            log_info(f"Reconexión intento {_retry + 1}/5...")
            time.sleep(3)
        else:
            log_warn("No se pudo reconectar tras cambio de cuenta")
            return {"switched": False, "reason": "reconnect_failed", "available_count": available_count}

        # 8. Verificar si ya está en chatgpt.com (el cambio de cuenta navega solo)
        #    Solo forzar navegación si NO está en chatgpt.com
        current_url = self.evaluate("window.location.href") or ""
        if "chatgpt.com" not in current_url.lower():
            log_info("Post-switch: no en ChatGPT, navegando...")
            self.evaluate("window.location.href = 'https://chatgpt.com/'")
            time.sleep(5)
            self._ws = None
            self.ws_url = ""
            for _retry in range(5):
                if self.connect():
                    break
                time.sleep(3)
        else:
            log_info(f"Post-switch: ya en ChatGPT ({current_url[:50]})")

        # 9. Esperar editor ready
        self.wait_for_page_ready(timeout_sec=30)

        log_ok(f"Cuenta cambiada a: {chosen['label']}")
        return {
            "switched": True,
            "account_id": chosen["accountId"],
            "account_label": chosen["label"],
            "available_count": available_count,
            "reason": "account_switched",
        }


# ── Función principal ────────────────────────────────────────────────────────

def paste_and_send_prompt(port: int, prompt: str, wait_response: bool = True,
                          timeout: int = 120, target_ws: str = "") -> dict:
    """
    Pega y envía un prompt en ChatGPT via CDP.

    Args:
        port: Puerto CDP del navegador con ChatGPT abierto
        prompt: Texto del prompt a enviar
        wait_response: Si True, espera la respuesta completa
        timeout: Timeout en segundos para la respuesta
        target_ws: WebSocket URL exacta de la tab a usar (evita buscar)

    Returns:
        dict con status, response (si wait_response=True), y detalles
    """
    session = ChatGPTSession(port=port)

    if target_ws:
        session.ws_url = target_ws
        try:
            import websockets.sync.client as ws_sync
            session._ws = ws_sync.connect(target_ws, max_size=2**22)
            log_ok(f"Conectado directo a tab: ...{target_ws[-40:]}")
        except Exception as e:
            log_warn(f"No se pudo conectar a target_ws: {e}")
            if not session.connect():
                return {"success": False, "error": f"No se pudo conectar a ChatGPT en puerto {port}"}
    elif not session.connect():
        return {"success": False, "error": f"No se pudo conectar a ChatGPT en puerto {port}"}

    try:
        # Esperar que la página esté lista
        if not session.wait_for_page_ready(timeout_sec=30):
            if session.last_error == "rate_limited":
                return _rate_limited_result(session)
            return {"success": False, "error": "ChatGPT no cargó completamente"}

        # Pegar prompt
        if not session.paste_prompt(prompt):
            return {"success": False, "error": "No se pudo pegar el prompt"}

        image_anchor = _capture_image_generation_anchor(session)

        # Enviar
        if not session.send_prompt():
            if session.last_error == "rate_limited":
                return _rate_limited_result(session)
            return {"success": False, "error": "No se pudo enviar el prompt"}

        _remember_image_generation_anchor(session.ws_url, image_anchor)

        result = {
            "success": True,
            "port": port,
            "prompt_length": len(prompt),
            "sent": True,
            "target_ws": session.ws_url,
            "image_anchor": image_anchor,
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


def paste_prompt_only(port: int, prompt: str, target_ws: str = "") -> dict:
    """
    Pega un prompt en ChatGPT sin enviarlo.

    Devuelve target_ws para que un paso posterior pueda reutilizar la misma tab
    y enviar el prompt más tarde.
    """
    session = ChatGPTSession(port=port)

    if target_ws:
        session.ws_url = target_ws
        try:
            import websockets.sync.client as ws_sync
            session._ws = ws_sync.connect(target_ws, max_size=2**22)
            log_ok(f"Conectado directo a tab: ...{target_ws[-40:]}")
        except Exception as e:
            log_warn(f"No se pudo conectar a target_ws: {e}")
            if not session.connect():
                return {"success": False, "error": f"No se pudo conectar a ChatGPT en puerto {port}"}
    elif not session.connect():
        return {"success": False, "error": f"No se pudo conectar a ChatGPT en puerto {port}"}

    try:
        if not session.wait_for_page_ready(timeout_sec=30):
            if session.last_error == "rate_limited":
                return _rate_limited_result(session)
            return {"success": False, "error": "ChatGPT no cargó completamente"}

        if not session.paste_prompt(prompt):
            return {"success": False, "error": "No se pudo pegar el prompt"}

        if session._check_rate_limited():
            session._mark_rate_limited()
            session._dismiss_rate_limit_modal()
            return _rate_limited_result(session)

        return {
            "success": True,
            "port": port,
            "prompt_length": len(prompt),
            "pasted": True,
            "sent": False,
            "target_ws": session.ws_url,
        }
    finally:
        session.close()


def send_pasted_prompt(
    port: int,
    wait_response: bool = True,
    timeout: int = 120,
    target_ws: str = "",
) -> dict:
    """
    Envía un prompt que ya está pegado en el editor de ChatGPT.

    Reutiliza la misma tab cuando target_ws está disponible y valida que aún
    haya contenido en el editor antes de intentar enviar.
    """
    session = ChatGPTSession(port=port)

    if target_ws:
        session.ws_url = target_ws
        try:
            import websockets.sync.client as ws_sync
            session._ws = ws_sync.connect(target_ws, max_size=2**22)
            log_ok(f"Conectado directo a tab: ...{target_ws[-40:]}")
        except Exception as e:
            log_warn(f"No se pudo conectar a target_ws: {e}")
            if not session.connect():
                return {"success": False, "error": f"No se pudo conectar a ChatGPT en puerto {port}"}
    elif not session.connect():
        return {"success": False, "error": f"No se pudo conectar a ChatGPT en puerto {port}"}

    try:
        if not session.wait_for_page_ready(timeout_sec=30):
            if session.last_error == "rate_limited":
                return _rate_limited_result(session)
            return {"success": False, "error": "ChatGPT no cargó completamente"}

        editor_text = session.evaluate("""(() => {
            const editor = document.querySelector('#prompt-textarea[contenteditable="true"]');
            return (editor?.innerText || '').trim();
        })()""") or ""
        if len(str(editor_text).strip()) < 3:
            return {
                "success": False,
                "error": "prompt_not_pasted",
                "message": "No hay prompt pegado en el editor",
                "target_ws": session.ws_url,
            }

        image_anchor = _capture_image_generation_anchor(session)

        if not session.send_prompt():
            if session.last_error == "rate_limited":
                return _rate_limited_result(session)
            return {"success": False, "error": "No se pudo enviar el prompt"}

        _remember_image_generation_anchor(session.ws_url, image_anchor)

        result = {
            "success": True,
            "port": port,
            "prompt_length": len(str(editor_text)),
            "pasted": True,
            "sent": True,
            "target_ws": session.ws_url,
            "image_anchor": image_anchor,
        }

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


MAX_ROTATION_ATTEMPTS = 5


def _rate_limited_result(session: ChatGPTSession, rotations: int = 0) -> dict:
    """Respuesta estándar cuando ChatGPT activa el rate limit temporal."""
    return {
        "success": False,
        "error": "rate_limited",
        "message": "ChatGPT limitó temporalmente el acceso a conversaciones",
        "cooldown_sec": _RATE_LIMIT_COOLDOWN_SEC,
        "rotations": rotations,
        "target_ws": session.ws_url,
    }


def paste_and_send_with_rotation(
    port: int, prompt: str, wait_response: bool = True, timeout: int = 120,
    target_ws: str = "",
) -> dict:
    """
    Pega y envía prompt con rotación automática de cuentas.

    Si ChatGPT responde 'sin tokens de imagen', cambia a otra cuenta
    dentro de ChatGPT y reintenta. Hasta 20 intentos.

    Si 'sesión expirada', retorna error para que el caller cambie perfil DiCloak.
    """
    session = ChatGPTSession(port=port)

    if target_ws:
        session.ws_url = target_ws
        try:
            import websockets.sync.client as ws_sync
            session._ws = ws_sync.connect(target_ws, max_size=2**22)
            log_ok(f"Conectado directo a tab: ...{target_ws[-40:]}")
        except Exception as e:
            log_warn(f"No se pudo conectar a target_ws: {e}")
            if not session.connect():
                return {"success": False, "error": f"No se pudo conectar a ChatGPT en puerto {port}"}
    elif not session.connect():
        return {"success": False, "error": f"No se pudo conectar a ChatGPT en puerto {port}"}

    last_account_id = ""
    last_account_label = ""
    rotations = 0

    try:
        for attempt in range(MAX_ROTATION_ATTEMPTS):
            log_info(f"Intento {attempt + 1}/{MAX_ROTATION_ATTEMPTS}")

            # Esperar página lista (60s para dar tiempo a que cargue)
            if not session.wait_for_page_ready(timeout_sec=60):
                if session.last_error == "rate_limited":
                    log_warn("Rate limit en page_ready — intentando rotar cuenta antes de rendirse...")
                    if last_account_id:
                        mark_exhausted(port, last_account_id, last_account_label)
                    exhausted = get_exhausted_ids(port)
                    switch_result = session.switch_account(exhausted)
                    if switch_result.get("switched"):
                        last_account_id = ""
                        last_account_label = ""
                        rotations += 1
                        log_ok(f"Rotación #{rotations} tras rate limit en page_ready.")
                        time.sleep(3)
                        continue
                    return _rate_limited_result(session, rotations=rotations)
                return {"success": False, "error": "ChatGPT no cargó completamente"}

            # Leer cuenta activa actual antes de enviar (para poder marcarla si falla)
            if not last_account_id:
                last_account_id = session.get_current_account_id()
                last_account_label = last_account_id

            # Pegar prompt
            if not session.paste_prompt(prompt):
                return {"success": False, "error": "No se pudo pegar el prompt"}

            image_anchor = _capture_image_generation_anchor(session)

            # Enviar
            if not session.send_prompt():
                if session.last_error == "rate_limited":
                    log_warn("Rate limit al enviar — intentando rotar cuenta...")
                    if last_account_id:
                        mark_exhausted(port, last_account_id, last_account_label)
                    exhausted = get_exhausted_ids(port)
                    switch_result = session.switch_account(exhausted)
                    if switch_result.get("switched"):
                        last_account_id = ""
                        last_account_label = ""
                        rotations += 1
                        log_ok(f"Rotación #{rotations} tras rate limit en envío.")
                        time.sleep(3)
                        continue
                    return _rate_limited_result(session, rotations=rotations)
                return {"success": False, "error": "No se pudo enviar el prompt"}

            # Detectar estado de tokens
            status = session.detect_token_status(timeout_sec=timeout)

            if status == "success":
                # Limpiar cuenta activa de exhausted si funcionó
                if last_account_id:
                    clear_exhausted(port, last_account_id)

                result = {
                    "success": True,
                    "port": port,
                    "prompt_length": len(prompt),
                    "sent": True,
                    "rotations": rotations,
                    "target_ws": session.ws_url,
                    "image_anchor": image_anchor,
                }
                _remember_image_generation_anchor(session.ws_url, image_anchor)
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

            if status == "rate_limited":
                # Antes de rendirse, intentar rotar cuenta dentro del mismo perfil.
                # Manualmente esto quita el rate limit — el rate limit es por cuenta,
                # no por perfil DICloak completo.
                log_warn("Rate limit detectado — intentando rotar cuenta antes de rendirse...")
                if last_account_id:
                    mark_exhausted(port, last_account_id, last_account_label)
                    log_info(f"Cuenta rate-limited marcada: {last_account_label or last_account_id}")
                exhausted = get_exhausted_ids(port)
                switch_result = session.switch_account(exhausted)
                if switch_result.get("switched"):
                    last_account_id = ""
                    last_account_label = ""
                    rotations += 1
                    log_ok(f"Rotación #{rotations} tras rate limit: {switch_result.get('account_label', '')}")
                    time.sleep(3)
                    continue  # reintentar con la nueva cuenta
                # Sin cuentas disponibles para rotar → rate limit real
                log_warn("Sin cuentas disponibles para rotar tras rate limit.")
                return _rate_limited_result(session, rotations=rotations)

            # no_image_tokens → marcar cuenta actual y rotar
            if last_account_id:
                mark_exhausted(port, last_account_id, last_account_label)
                log_info(f"Cuenta agotada marcada: {last_account_label or last_account_id}")

            log_info("Tokens agotados. Rotando cuenta...")
            exhausted = get_exhausted_ids(port)
            switch_result = session.switch_account(exhausted)
            # Resetear para que el próximo intento lea la nueva cuenta activa
            last_account_id = ""
            last_account_label = ""

            if not switch_result.get("switched"):
                reason = switch_result.get("reason", "unknown")
                error_code = "all_accounts_exhausted" if reason == "no_candidates" else "account_switch_ui_failed"
                message = (
                    f"Sin cuentas disponibles: {reason}"
                    if error_code == "all_accounts_exhausted"
                    else f"Falló la UI de cambio de cuenta: {reason}"
                )
                return {
                    "success": False,
                    "error": error_code,
                    "message": message,
                    "rotations": rotations,
                    "reason": reason,
                    "available_count": switch_result.get("available_count", 0),
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
