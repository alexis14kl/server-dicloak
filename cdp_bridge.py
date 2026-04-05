"""
CDP Bridge — Controla DICloak via Chrome DevTools Protocol.

Mantiene conexión WebSocket persistente para evitar reconexiones lentas.
Standalone — no depende de core.
"""
from __future__ import annotations

import json
import re
import time
import urllib.request
from dataclasses import dataclass

from logger import log_info, log_ok, log_warn, log_error


DEFAULT_DICLOAK_PORT = 9333


@dataclass
class ProfileInfo:
    id: str
    name: str
    status: str = "stopped"
    debug_port: int = 0
    ws_url: str = ""
    pid: int = 0


# ── HTTP helpers ─────────────────────────────────────────────────────────────

def _http_get_json(url: str, timeout: int = 5) -> dict | list:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _test_cdp_port(port: int) -> bool:
    try:
        data = _http_get_json(f"http://127.0.0.1:{port}/json/version", timeout=2)
        return "webSocketDebuggerUrl" in str(data)
    except Exception:
        return False


def is_dicloak_ready(port: int = DEFAULT_DICLOAK_PORT) -> bool:
    return _test_cdp_port(port)


def get_dicloak_targets(port: int = DEFAULT_DICLOAK_PORT) -> list[dict]:
    try:
        return _http_get_json(f"http://127.0.0.1:{port}/json")
    except Exception:
        return []


def _get_page_ws_url(port: int = DEFAULT_DICLOAK_PORT) -> str:
    targets = get_dicloak_targets(port)
    for t in targets:
        if t.get("type") == "page":
            return t.get("webSocketDebuggerUrl", "")
    return ""


# ── Persistent CDP Connection ────────────────────────────────────────────────

class CDPConnection:
    """Conexión WebSocket persistente al CDP de DiCloak (9333)."""

    def __init__(self, port: int = DEFAULT_DICLOAK_PORT):
        self.port = port
        self._ws = None
        self._msg_id = 0

    def connect(self) -> bool:
        try:
            import websockets.sync.client as ws_sync
        except ImportError:
            import subprocess, sys
            subprocess.check_call([sys.executable, "-m", "pip", "install", "websockets", "-q"])
            import websockets.sync.client as ws_sync

        ws_url = _get_page_ws_url(self.port)
        if not ws_url:
            log_warn(f"No se encontró WebSocket URL en puerto {self.port}")
            return False

        try:
            self._ws = ws_sync.connect(ws_url, max_size=2**22)
            log_ok(f"CDP conectado: {ws_url[:60]}")
            return True
        except Exception as e:
            log_warn(f"Error conectando CDP WebSocket: {e}")
            self._ws = None
            return False

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

    def evaluate(self, expression: str, timeout: int = 8) -> str | None:
        if not self._ensure_connected():
            return None

        self._msg_id += 1
        msg = json.dumps({
            "id": self._msg_id,
            "method": "Runtime.evaluate",
            "params": {"expression": expression, "returnByValue": True}
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


# Conexión global
_cdp: CDPConnection | None = None


def get_cdp(port: int = DEFAULT_DICLOAK_PORT) -> CDPConnection:
    global _cdp
    if _cdp is None or _cdp.port != port:
        _cdp = CDPConnection(port)
    return _cdp


def init_cdp(port: int = DEFAULT_DICLOAK_PORT) -> bool:
    cdp = get_cdp(port)
    if not cdp.connect():
        return False
    ok = inject_cdp_hook(port)
    if ok:
        log_ok("Hook CDP inyectado al iniciar servidor")
    else:
        log_warn("No se pudo inyectar hook CDP al iniciar")
    return True


def cdp_evaluate_sync(expression: str, port: int = DEFAULT_DICLOAK_PORT, timeout: int = 8) -> str | None:
    return get_cdp(port).evaluate(expression, timeout)


# ── Profile Operations via CDP ───────────────────────────────────────────────

def _ensure_on_profile_list(port: int = DEFAULT_DICLOAK_PORT) -> bool:
    """Navega a la lista de perfiles si DICloak está en otra página."""
    check_js = "location.hash.includes('envList') || location.hash.includes('environment')"
    result = cdp_evaluate_sync(check_js, port)
    if result == "true":
        return True

    cdp_evaluate_sync("location.hash = '#/environment/envList'", port, timeout=5)
    import time
    time.sleep(2)
    return True


def list_profiles_via_cdp(port: int = DEFAULT_DICLOAK_PORT) -> list[ProfileInfo]:
    _ensure_on_profile_list(port)

    js = """(() => {
        try {
            const rows = document.querySelectorAll('.el-table__row');
            const profiles = [];
            rows.forEach(row => {
                const cells = Array.from(row.querySelectorAll('td'));
                const texts = cells.map(c => (c.textContent || '').trim()).filter(t => t.length > 1);
                // Nombre perfil = primera celda que contiene letras (no solo numeros)
                const name = texts.find(t => /[a-zA-Z]/.test(t)) || '';
                if (name) {
                    profiles.push({ id: name, name, status: '' });
                }
            });
            return JSON.stringify(profiles);
        } catch(e) {
            return JSON.stringify({error: e.message});
        }
    })()"""

    result = cdp_evaluate_sync(js, port)
    if not result:
        return []
    try:
        items = json.loads(result)
        return [ProfileInfo(id=p.get("id", ""), name=p["name"], status=p.get("status", "")) for p in items if p.get("name")]
    except Exception:
        return []


HOOK_JS = r"""(() => {
    if (window.__CDP_HOOK_INSTALLED__) return 'ALREADY_INSTALLED';
    window.__CDP_HOOK_INSTALLED__ = true;

    const { ipcRenderer } = require('electron');

    const _origInvoke = ipcRenderer.invoke.bind(ipcRenderer);
    ipcRenderer.invoke = function(channel, ...args) {
        for (const arg of args) {
            if (arg && typeof arg === 'object') {
                const force = (o) => {
                    if (!o || typeof o !== 'object') return;
                    if ('canIuseCdp' in o) o.canIuseCdp = true;
                    if (o.openParams && 'canIuseCdp' in o.openParams) o.openParams.canIuseCdp = true;
                    Object.values(o).forEach(v => { if (v && typeof v === 'object' && v !== o) force(v); });
                };
                force(arg);
            }
        }
        return _origInvoke(channel, ...args);
    };

    const _origSend = ipcRenderer.send.bind(ipcRenderer);
    ipcRenderer.send = function(channel, ...args) {
        for (const arg of args) {
            if (arg && typeof arg === 'object') {
                const force = (o) => {
                    if (!o || typeof o !== 'object') return;
                    if ('canIuseCdp' in o) o.canIuseCdp = true;
                    Object.values(o).forEach(v => { if (v && typeof v === 'object' && v !== o) force(v); });
                };
                force(arg);
            }
        }
        return _origSend(channel, ...args);
    };

    return 'HOOK_INSTALLED';
})()"""


def inject_cdp_hook(port: int = DEFAULT_DICLOAK_PORT) -> bool:
    result = cdp_evaluate_sync(HOOK_JS, port)
    return result is not None and "INSTALLED" in str(result).upper()


def _close_zombie_profiles_via_cdp(port: int = DEFAULT_DICLOAK_PORT) -> None:
    """Cierra perfiles zombie (muestran 'Ver' pero no tienen proceso vivo).
    Selecciona todos los perfiles con 'Ver' y simula cierre via taskkill + reload de tabla.
    """
    import subprocess, sys as _sys

    # Matar cualquier ginsbrowser residual
    try:
        if _sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/IM", "ginsbrowser.exe"],
                           capture_output=True, timeout=5)
        else:
            subprocess.run(["pkill", "-f", "ginsbrowser"],
                           capture_output=True, timeout=5)
    except Exception:
        pass

    import time as _time
    _time.sleep(2)

    # Volver a la lista de perfiles
    _ensure_on_profile_list(port)
    _time.sleep(2)

    # Verificar si queda algún 'Ver'
    check = cdp_evaluate_sync("""(() => {
        const rows = document.querySelectorAll('.el-table__row');
        let zombie = 0;
        for (const row of rows) {
            const btns = Array.from(row.querySelectorAll('button'));
            if (btns.some(b => (b.textContent||'').trim() === 'Ver')) zombie++;
        }
        return String(zombie);
    })()""", port, timeout=5)

    if check and int(check or "0") > 0:
        log_warn(f"Aún hay {check} perfil(es) zombie después de limpiar.")
    else:
        log_ok("Perfiles zombie limpiados")


def open_profile_via_cdp(profile_name: str, port: int = DEFAULT_DICLOAK_PORT) -> str:
    """Abre un perfil en DiCloak via CDP.

    Retorna:
        'CLICKED_OPEN' — se hizo click en Abrir
        'ALREADY_OPEN' — perfil ya abierto (boton Ver/Abriendo)
        'PROFILE_NOT_FOUND' — no se encontro el perfil
        'NO_OPEN_BUTTON: ...' — no hay boton reconocido
    """
    safe_name = profile_name.replace("'", "\\'").replace('"', '\\"')

    open_js = f"""(() => {{
        try {{
            const targetName = "{safe_name}".toLowerCase().trim();
            const rows = document.querySelectorAll('.el-table__row');
            let targetRow = null;

            for (const row of rows) {{
                const cells = Array.from(row.querySelectorAll('td .cell'));
                const nameCell = (cells[2]?.innerText || '').trim();
                if (nameCell.toLowerCase() === targetName || nameCell.toLowerCase().includes(targetName) || targetName.includes(nameCell.toLowerCase())) {{
                    targetRow = row;
                    break;
                }}
            }}

            if (!targetRow) return 'PROFILE_NOT_FOUND';

            const buttons = Array.from(targetRow.querySelectorAll('button, a, [role="button"], .el-button'));
            const btnTexts = buttons.map(b => (b.innerText || b.textContent || '').trim().toLowerCase());

            // 1. Boton "Abrir" — perfil cerrado, hay que abrirlo
            const openIdx = btnTexts.findIndex(t => t === 'abrir' || t === 'open' || t === 'launch' || t === 'iniciar');
            if (openIdx >= 0) {{ buttons[openIdx].click(); return 'CLICKED_OPEN'; }}

            // 2. Perfil ya abierto — cualquier indicador (Ver, Cerrar, Abriendo, etc.)
            const alreadyOpen = btnTexts.findIndex(t =>
                t === 'ver' || t === 'view' ||
                t === 'cerrar' || t === 'close' ||
                t === 'abriendo' || t === 'abriendo...' || t === 'opening' || t === 'loading'
            );
            if (alreadyOpen >= 0) return 'ALREADY_OPEN';

            return 'NO_OPEN_BUTTON: ' + btnTexts.join(', ');
        }} catch(e) {{ return 'ERROR: ' + e.message; }}
    }})()"""

    result = str(cdp_evaluate_sync(open_js, port, timeout=5) or "")

    if "CLICKED_OPEN" in result:
        log_ok(f"Perfil '{profile_name}' abierto via CDP")
        return "CLICKED_OPEN"

    if "ALREADY_OPEN" in result:
        log_info(f"Perfil '{profile_name}' ya esta abierto")
        return "ALREADY_OPEN"

    if "PROFILE_NOT_FOUND" in result:
        log_warn(f"Perfil '{profile_name}' no encontrado en la lista")
        return "PROFILE_NOT_FOUND"

    log_warn(f"No se pudo abrir perfil '{profile_name}': {result}")
    return result


def detect_ginsbrowser_port(timeout_sec: int = 60) -> int:
    from platform_utils import read_cdp_debug_info

    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        data = read_cdp_debug_info()
        for entry in data.values():
            if not isinstance(entry, dict):
                continue
            try:
                port = int(entry.get("debugPort") or entry.get("port") or 0)
            except (TypeError, ValueError):
                continue
            if port and _test_cdp_port(port):
                return port
        time.sleep(0.5)
    return 0
