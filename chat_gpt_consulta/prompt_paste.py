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
        except Exception:
            pass
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

    def close(self):
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass
            self._ws = None

    # ── Acciones de ChatGPT ──────────────────────────────────────────────

    def wait_for_page_ready(self, timeout_sec: int = 60) -> bool:
        """Espera que ChatGPT cargue completamente (sin CAPTCHA)."""
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            title = self.evaluate("document.title") or ""
            if "un momento" in title.lower() or "just a moment" in title.lower():
                log_info("Esperando CAPTCHA de Cloudflare...")
                time.sleep(2)
                continue
            if not title or title == "about:blank":
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
        """Pega el prompt en el editor de ChatGPT por chunks."""
        # Limpiar editor
        self.evaluate("""(() => {
            const editor = document.querySelector('#prompt-textarea[contenteditable="true"]');
            if (editor) { editor.focus(); editor.innerHTML = '<p><br></p>'; }
        })()""")
        time.sleep(0.3)

        # Pegar por chunks usando InputEvent (simula tipeo)
        chunk_size = 200
        for i in range(0, len(prompt), chunk_size):
            chunk = prompt[i:i + chunk_size].replace("\\", "\\\\").replace("`", "\\`").replace("$", "\\$").replace("\n", "\\n").replace("\r", "")
            self.evaluate(f"""(() => {{
                const editor = document.querySelector('#prompt-textarea[contenteditable="true"]');
                if (!editor) return 'NO_EDITOR';
                editor.focus();
                document.execCommand('insertText', false, `{chunk}`);
                return 'OK';
            }})()""")
            if i + chunk_size < len(prompt):
                time.sleep(0.05)

        # Verificar que se pegó
        time.sleep(0.5)
        text = self.evaluate("""(() => {
            const editor = document.querySelector('#prompt-textarea[contenteditable="true"]');
            return (editor?.innerText || '').trim().substring(0, 100);
        })()""") or ""

        if len(text) > 10:
            log_ok(f"Prompt pegado ({len(prompt)} chars)")
            return True

        log_warn("El prompt no se registró en el editor")
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

        # Fallback: Enter
        log_info("Botón no encontrado, intentando Enter...")
        self.evaluate("""(() => {
            const editor = document.querySelector('#prompt-textarea[contenteditable="true"]');
            if (editor) {
                editor.focus();
                const event = new KeyboardEvent('keydown', {key: 'Enter', code: 'Enter', bubbles: true});
                editor.dispatchEvent(event);
            }
        })()""")
        time.sleep(1)
        return True

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
