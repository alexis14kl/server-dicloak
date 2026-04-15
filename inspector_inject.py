"""
inspector_inject.py — Modo "attach" para DICloak corriendo sin debug
====================================================================

Permite al server.py controlar una instancia de DICloak abierta MANUALMENTE
(sin --remote-debugging-port), sin necesidad de matarla y relanzarla.

Tecnica:
  1. Buscar el PID del main process de Electron de DICloak (sin --type=).
  2. Activar el inspector V8 en caliente con `node -e 'process._debugProcess(pid)'`.
     (process._debugProcess usa DebugBreakProcess en Win32 — abre :9229)
  3. Conectarse al inspector V8 via WebSocket.
  4. Inyectar el HOOK_JS en el renderer principal usando
     `webContents.executeJavaScript()` desde el main process.
  5. Monkey-patchear las funciones de cdp_bridge (cdp_evaluate_sync,
     inject_cdp_hook, is_dicloak_ready, init_cdp, _get_page_ws_url) para
     que el resto del codigo del bot funcione SIN cambios — todas las
     llamadas se enrutan via inspector V8 + webContents.executeJavaScript.

Uso desde server.py:

    from inspector_inject import try_attach_existing_dicloak

    if is_dicloak_ready(9333):
        ...   # modo CDP normal (DICloak lanzado por nosotros)
    elif try_attach_existing_dicloak():
        ...   # modo INSPECTOR (DICloak abierto manualmente, attached)
    else:
        ...   # lanzar DICloak con --remote-debugging-port (flujo original)

Despues de un attach exitoso, el modo queda activo durante toda la sesion
y todas las llamadas existentes a cdp_evaluate_sync siguen funcionando.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from typing import Any, Optional

from logger import log_error, log_info, log_ok, log_warn


DEFAULT_INSPECTOR_PORT = 9229
DICLOAK_PROCESS_NAME = "DICloak.exe"


# ── Discovery ────────────────────────────────────────────────────────────────

def find_dicloak_main_pid(wait_for_window: float = 5.0) -> Optional[int]:
    """Busca el PID del main process de Electron de DICloak.

    Preferencia (en orden):
      1. Main process con ventana visible (MainWindowHandle != 0).
         Este es el DICloak "activo" con el que interactua el usuario.
      2. Si ninguno tiene ventana, ESPERA hasta `wait_for_window` segundos
         porque un DICloak recien lanzado tarda ~2-4s en mostrar su ventana
         (MainWindowHandle sigue en 0 durante el startup de Electron).
      3. Si tras esperar sigue sin ventana, retorna el main mas reciente
         como fallback (mejor tirar contra uno en transicion que nada).

    Si hay MULTIPLES DICloak corriendo (duplicados por abrir/cerrar mal),
    esto evita engancharnos al zombi.
    """
    if sys.platform != "win32":
        return None

    # Busca mains con ventana visible. Retorna PID o None.
    def _query_main_with_window() -> Optional[int]:
        ps_cmd = (
            f"$mains = Get-CimInstance Win32_Process | "
            f"Where-Object {{ $_.Name -eq '{DICLOAK_PROCESS_NAME}' -and "
            f"$_.CommandLine -notlike '*--type=*' }}; "
            f"$withWindow = $mains | ForEach-Object {{ "
            f"  $p = Get-Process -Id $_.ProcessId -ErrorAction SilentlyContinue; "
            f"  if ($p -and $p.MainWindowHandle -ne 0) {{ "
            f"    [PSCustomObject]@{{ PID = $_.ProcessId; Start = $_.CreationDate }} "
            f"  }} "
            f"}}; "
            f"if ($withWindow) {{ "
            f"  ($withWindow | Sort-Object Start -Descending | Select-Object -First 1).PID "
            f"}}"
        )
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_cmd],
                capture_output=True, text=True, timeout=10,
            )
            out = result.stdout.strip()
            return int(out) if out.isdigit() else None
        except Exception:
            return None

    # Paso 1: intento rapido
    pid = _query_main_with_window()
    if pid is not None:
        return pid

    # Paso 2: polling durante wait_for_window segundos — el DICloak puede
    # estar recien arrancado y aun no mostrar ventana.
    deadline = time.time() + wait_for_window
    while time.time() < deadline:
        time.sleep(0.5)
        pid = _query_main_with_window()
        if pid is not None:
            log_info(f"DICloak main PID {pid} detectado tras espera de ventana")
            return pid

    # Paso 3: fallback — retornar el main mas reciente aunque no tenga
    # ventana todavia. Mejor intentar contra uno en transicion que fallar
    # silenciosamente.
    try:
        ps_cmd_fallback = (
            f"Get-CimInstance Win32_Process | "
            f"Where-Object {{ $_.Name -eq '{DICLOAK_PROCESS_NAME}' -and "
            f"$_.CommandLine -notlike '*--type=*' }} | "
            f"Sort-Object CreationDate -Descending | "
            f"Select-Object -First 1 -ExpandProperty ProcessId"
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_cmd_fallback],
            capture_output=True, text=True, timeout=10,
        )
        out = result.stdout.strip()
        if out and out.isdigit():
            log_warn(f"DICloak main PID {out} sin ventana visible — usando fallback")
            return int(out)
    except Exception as exc:
        log_warn(f"Error buscando DICloak main PID (fallback): {exc}")
    return None


def find_all_dicloak_main_pids() -> list[int]:
    """Lista TODOS los main processes de DICloak corriendo (sin --type=).

    Util para detectar duplicados zombi.
    """
    if sys.platform != "win32":
        return []
    try:
        ps_cmd = (
            f"Get-CimInstance Win32_Process | "
            f"Where-Object {{ $_.Name -eq '{DICLOAK_PROCESS_NAME}' -and "
            f"$_.CommandLine -notlike '*--type=*' }} | "
            f"Select-Object -ExpandProperty ProcessId"
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_cmd],
            capture_output=True, text=True, timeout=10,
        )
        return [int(line.strip()) for line in result.stdout.splitlines() if line.strip().isdigit()]
    except Exception:
        return []


def kill_dicloak_zombies(min_age_seconds: int = 30) -> int:
    """Mata DICloak main processes SIN ventana visible y ANTIGUOS (zombis reales).

    Esto previene que find_dicloak_main_pid() elija un proceso muriendose
    o ya huerfano cuando el usuario cerro/reabrio DICloak varias veces.

    IMPORTANTE: solo mata procesos que cumplen las TRES condiciones:
      1. main process (sin --type= en cmdline)
      2. sin ventana visible (MainWindowHandle == 0)
      3. mas viejos que min_age_seconds (default 30s)

    La condicion #3 es critica: un DICloak recien lanzado todavia no ha
    mostrado su ventana (MainWindowHandle == 0 durante ~2-4s de startup).
    Si no excluimos los recientes, mataremos el DICloak que el usuario
    acaba de abrir justo antes de que su ventana aparezca — exactamente
    el escenario "cierro DICloak y lo abro de nuevo mientras el server
    esta corriendo".

    Ademas, si TODOS los mains actuales son nuevos (< min_age_seconds),
    no mata nada — asumimos que uno de ellos va a ser el activo pronto.

    Retorna el numero de zombies matados.
    """
    if sys.platform != "win32":
        return 0
    try:
        ps_cmd = (
            f"$killed = 0; "
            f"$mains = Get-CimInstance Win32_Process | "
            f"  Where-Object {{ $_.Name -eq '{DICLOAK_PROCESS_NAME}' -and "
            f"  $_.CommandLine -notlike '*--type=*' }}; "
            f"$now = Get-Date; "
            f"foreach ($m in $mains) {{ "
            f"  $p = Get-Process -Id $m.ProcessId -ErrorAction SilentlyContinue; "
            f"  if (-not $p) {{ continue }}; "
            f"  $age = ($now - $m.CreationDate).TotalSeconds; "
            f"  if ($p.MainWindowHandle -eq 0 -and $age -gt {min_age_seconds}) {{ "
            f"    Stop-Process -Id $m.ProcessId -Force -ErrorAction SilentlyContinue; "
            f"    $killed++ "
            f"  }} "
            f"}}; "
            f"$killed"
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_cmd],
            capture_output=True, text=True, timeout=10,
        )
        out = result.stdout.strip()
        return int(out) if out.isdigit() else 0
    except Exception as exc:
        log_warn(f"Error matando zombies DICloak: {exc}")
        return 0


# ── Inspector V8 activation ──────────────────────────────────────────────────

def enable_v8_inspector(pid: int) -> bool:
    """Abre el inspector V8 de un proceso Node corriendo, en caliente.

    Usa `node -e 'process._debugProcess(pid)'`. En Windows esto invoca
    DebugBreakProcess (Win32 API) sobre el PID — el target abre el
    inspector en 127.0.0.1:9229 sin reiniciarse.
    """
    node_exe = shutil.which("node") or shutil.which("node.exe")
    if not node_exe:
        log_error("Node.js no encontrado en PATH (necesario para process._debugProcess)")
        return False
    try:
        result = subprocess.run(
            [node_exe, "-e", f"process._debugProcess({pid})"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            log_warn(f"_debugProcess fallo (rc={result.returncode}): {result.stderr.strip()}")
            return False
        return True
    except Exception as exc:
        log_warn(f"Error llamando _debugProcess: {exc}")
        return False


def wait_for_inspector(port: int = DEFAULT_INSPECTOR_PORT, timeout: float = 10.0) -> bool:
    """Espera a que el inspector V8 abra el puerto HTTP."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(0.2)
    return False


def get_inspector_ws_url(port: int = DEFAULT_INSPECTOR_PORT) -> Optional[str]:
    """Obtiene el WebSocket URL del target unico del inspector V8 (main process)."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=3) as r:
            data = json.loads(r.read().decode("utf-8"))
        if isinstance(data, list) and data:
            return data[0].get("webSocketDebuggerUrl")
    except Exception as exc:
        log_warn(f"Error leyendo /json del inspector: {exc}")
    return None


# ── Inspector client ─────────────────────────────────────────────────────────

class InspectorClient:
    """Cliente WebSocket persistente al inspector V8 del main de DICloak.

    Permite ejecutar JS en el main process, y como puente, ejecutar JS en
    cualquier renderer via webContents.executeJavaScript().
    """

    def __init__(self, ws_url: str, pid: Optional[int] = None):
        self.ws_url = ws_url
        self.pid = pid  # PID del main process al que apunta esta conexion
        self._ws = None
        self._msg_id = 0
        self._lock = threading.Lock()

    def connect(self) -> bool:
        try:
            import websockets.sync.client as ws_sync
        except ImportError:
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", "websockets", "-q"]
            )
            import websockets.sync.client as ws_sync

        try:
            self._ws = ws_sync.connect(self.ws_url, max_size=2**22)
        except Exception as exc:
            log_error(f"Error conectando al inspector V8: {exc}")
            return False

        # Habilitar Runtime para poder ejecutar JS
        try:
            self._call("Runtime.enable")
            return True
        except Exception as exc:
            log_error(f"Error habilitando Runtime en inspector: {exc}")
            self.close()
            return False

    def is_connected(self) -> bool:
        """Verifica que la conexion sea USABLE (no solo que el WS este vivo).

        Chequea tres cosas:
          1. El WebSocket responde al ping (con id match — evita falsos
             positivos con eventos async del inspector).
          2. `webContents.getAllWebContents()` devuelve al menos un renderer
             viable. Si el usuario cerro DICloak con la X (destruye ventana
             pero el proceso sigue), el main process vive y el WS responde,
             PERO los webContents desaparecen. En ese caso la conexion esta
             "alive pero inutil" y hay que re-attach al nuevo DICloak.
        """
        if self._ws is None:
            return False
        try:
            with self._lock:
                self._msg_id += 1
                msg_id = self._msg_id
                # Un solo evaluate que chequea ambas cosas: ping + webContents count
                check_js = (
                    "(function(){"
                    "try{"
                    "var wc=process.mainModule.require('electron').webContents;"
                    "var all=wc.getAllWebContents();"
                    "var viable=all.filter(function(w){"
                    "try{var u=w.getURL()||'';return !u.startsWith('devtools://')&&!u.startsWith('chrome-extension://');}catch(e){return false;}"
                    "});"
                    "return viable.length;"
                    "}catch(e){return -1;}"
                    "})()"
                )
                self._ws.send(json.dumps({
                    "id": msg_id,
                    "method": "Runtime.evaluate",
                    "params": {"expression": check_js, "returnByValue": True},
                }))
                # Drenar mensajes hasta encontrar la respuesta con este id
                import time as _t
                deadline = _t.time() + 3.0
                while _t.time() < deadline:
                    remaining = max(0.1, deadline - _t.time())
                    resp_raw = self._ws.recv(timeout=remaining)
                    try:
                        resp = json.loads(resp_raw)
                    except Exception:
                        continue
                    if resp.get("id") == msg_id:
                        val = resp.get("result", {}).get("result", {}).get("value")
                        # val es el numero de webContents viables (0+) o -1 si error
                        if isinstance(val, int) and val > 0:
                            return True
                        # WS vivo pero sin renderers viables — conexion inutil
                        return False
                # No llego respuesta a tiempo — conexion degradada o muerta
                self._ws = None
                return False
        except Exception:
            self._ws = None
            return False

    def close(self) -> None:
        if self._ws is not None:
            try:
                self._ws.close()
            except Exception:
                pass
            self._ws = None

    def _call(self, method: str, params: Optional[dict] = None, timeout: float = 10.0) -> dict:
        """Envia un comando CDP/inspector y espera la respuesta con el mismo id."""
        with self._lock:
            self._msg_id += 1
            msg_id = self._msg_id
            payload = json.dumps({"id": msg_id, "method": method, "params": params or {}})
            self._ws.send(payload)
            # Drain hasta encontrar la respuesta de este id
            while True:
                resp_raw = self._ws.recv(timeout=timeout)
                resp = json.loads(resp_raw)
                if resp.get("id") == msg_id:
                    return resp

    def evaluate_in_main(
        self,
        expression: str,
        await_promise: bool = False,
        timeout: float = 10.0,
    ) -> Any:
        """Ejecuta JS en el main process. Retorna el value (puede ser None)."""
        if not self.is_connected():
            return None
        try:
            r = self._call(
                "Runtime.evaluate",
                {
                    "expression": expression,
                    "returnByValue": True,
                    "awaitPromise": await_promise,
                },
                timeout=timeout,
            )
        except Exception as exc:
            log_warn(f"Inspector evaluate error: {exc}")
            return None

        result = r.get("result", {}).get("result", {})
        if result.get("subtype") == "error":
            log_warn(f"JS error en main: {result.get('description', '')[:200]}")
            return None
        return result.get("value")

    def evaluate_in_renderer(self, js: str, timeout: float = 10.0) -> Optional[str]:
        """Ejecuta JS en el renderer principal de DICloak via puente main → renderer.

        Equivalente funcional a `cdp_evaluate_sync()` pero usando el inspector
        V8 + webContents.executeJavaScript() en lugar de CDP del renderer.
        """
        if not self.is_connected():
            return None

        # Embebemos el JS del usuario como string JSON para escapar correctamente.
        escaped_js = json.dumps(js)
        wrapper = f"""
(async function() {{
    const electron = process.mainModule.require('electron');
    const all = electron.webContents.getAllWebContents();
    if (all.length === 0) return null;
    // Preferir webContents que NO sean devtools/extension
    const target = all.find(wc => {{
        try {{
            const url = wc.getURL() || '';
            return !url.startsWith('devtools://') && !url.startsWith('chrome-extension://');
        }} catch (e) {{ return false; }}
    }}) || all[0];
    try {{
        const r = await target.executeJavaScript({escaped_js}, true);
        if (r === undefined || r === null) return null;
        return typeof r === 'object' ? JSON.stringify(r) : String(r);
    }} catch (e) {{
        return 'INSPECTOR_EVAL_ERROR: ' + e.message;
    }}
}})()
"""
        result = self.evaluate_in_main(wrapper, await_promise=True, timeout=timeout)
        if result is None:
            return None
        if isinstance(result, str) and result.startswith("INSPECTOR_EVAL_ERROR"):
            log_warn(result[:200])
            return None
        return str(result)

    def inject_hook(self, hook_js: str) -> bool:
        """Inyecta el HOOK_JS en el renderer de DICloak."""
        result = self.evaluate_in_renderer(hook_js)
        if result and "INSTALLED" in str(result).upper():
            return True
        log_warn(f"Hook injection retorno: {result!r}")
        return False

    def list_profiles_with_serial(self, timeout: float = 5.0) -> list[dict]:
        """Lee el DOM de DICloak y retorna [{'serialNumber', 'name'}] de cada perfil.

        El serialNumber es el numero de la primera columna (ej. "98") —
        coincide con el campo serialNumber de cdp_debug_info.json, asi que
        sirve para matchear perfiles abiertos con su puerto CDP sin adivinar.
        """
        js = (
            "(() => {"
            "try {"
            "  const rows = document.querySelectorAll('.el-table__row');"
            "  const profiles = [];"
            "  rows.forEach(row => {"
            "    const cells = Array.from(row.querySelectorAll('td .cell'));"
            "    const texts = cells.map(c => (c.innerText || c.textContent || '').trim());"
            "    let serial = null;"
            "    for (const t of texts) {"
            "      if (/^\\d+$/.test(t)) { serial = parseInt(t); break; }"
            "    }"
            "    const name = texts.find(t => /[a-zA-Z]/.test(t)) || '';"
            "    if (name && serial !== null) {"
            "      profiles.push({serialNumber: serial, name: name});"
            "    }"
            "  });"
            "  return JSON.stringify(profiles);"
            "} catch(e) { return JSON.stringify({error: e.message}); }"
            "})()"
        )
        result = self.evaluate_in_renderer(js, timeout=timeout)
        if not result:
            return []
        try:
            data = json.loads(result)
            if isinstance(data, list):
                return data
        except Exception:
            pass
        return []


# ── Estado global del modo attach ────────────────────────────────────────────

_client: Optional[InspectorClient] = None
_active = False
_state_lock = threading.Lock()


def get_client() -> Optional[InspectorClient]:
    return _client


def is_active() -> bool:
    return _active


def _test_port_alive(port: int) -> bool:
    """Test rapido: el puerto CDP responde a /json/version."""
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{int(port)}/json/version",
            timeout=2,
        ) as r:
            return r.status == 200
    except Exception:
        return False


def find_active_cdp_for_profile(name: str) -> Optional[int]:
    """Retorna el puerto CDP de un perfil ya abierto, o None si no esta corriendo.

    Estrategia de match (robusta para nombres ambiguos como "Super Grok"
    cuando hay varios perfiles "#N Super Grok"):

      1. Lee cdp_debug_info.json → mapa de serialNumbers con CDP vivo.
      2. Lee el DOM de DICloak → lista completa [{serialNumber, name}, ...].
      3. Matchea query contra nombres (substring, case-insensitive).
      4. Entre los matches, PREFIERE los que tienen CDP activo en el json.
      5. Si hay un candidato con CDP activo que responde → retorna su puerto.
      6. Si no, None (caller hara el flujo normal de apertura).

    No hace click en nada. No abre perfiles. Solo descubre estado existente.
    """
    if _client is None or not _client.is_connected():
        log_warn("find_active_cdp: inspector client no disponible")
        return None

    try:
        from platform_utils import read_cdp_debug_info
    except Exception as exc:
        log_warn(f"find_active_cdp: no se pudo importar read_cdp_debug_info: {exc}")
        return None

    # 1. Mapa serialNumber → entry (solo entries con CDP vivo)
    data = read_cdp_debug_info()
    active_by_serial: dict[int, dict] = {}
    for entry in data.values():
        if not isinstance(entry, dict):
            continue
        try:
            serial = int(entry.get("serialNumber"))
            port = int(entry.get("debugPort") or 0)
        except (TypeError, ValueError):
            continue
        if port and _test_port_alive(port):
            active_by_serial[serial] = entry

    if not active_by_serial:
        log_info(f"find_active_cdp: ningun perfil con CDP vivo en cdp_debug_info.json")
        return None

    # 2. Lista completa desde el DOM
    profiles = _client.list_profiles_with_serial()
    if not profiles:
        log_warn("find_active_cdp: list_profiles_with_serial retorno vacio")
        return None

    # 3. Match por substring (todas las palabras del query dentro del name)
    query_words = [w for w in name.lower().strip().split() if w]
    matched = []
    for p in profiles:
        pname = str(p.get("name", "")).lower()
        if all(w in pname for w in query_words):
            matched.append(p)

    if not matched:
        log_info(f"find_active_cdp: nombre '{name}' no matchea ningun perfil del DOM")
        return None

    # 4. Preferir candidatos con CDP activo
    active_candidates = [
        p for p in matched
        if p.get("serialNumber") in active_by_serial
    ]

    if not active_candidates:
        names = [p.get("name", "?") for p in matched[:3]]
        log_info(
            f"find_active_cdp: '{name}' matchea {len(matched)} perfil(es) ({names}) "
            "pero ninguno tiene CDP vivo — flujo normal de apertura"
        )
        return None

    # 5. Retornar el primer candidato activo
    chosen = active_candidates[0]
    serial = chosen.get("serialNumber")
    entry = active_by_serial[serial]
    port = int(entry.get("debugPort"))
    log_ok(
        f"find_active_cdp: '{name}' → '{chosen.get('name')}' "
        f"(serial {serial}, puerto {port})"
    )
    return port


# ── Monkey-patching de cdp_bridge ────────────────────────────────────────────

def _replace_function_globally(module_name: str, func_name: str, new_func: Any) -> None:
    """Reemplaza una funcion en su modulo origen Y en todos los modulos que la
    importaron directamente con `from <mod> import <func>`. Necesario porque
    Python copia la referencia al hacer `from X import Y`, asi que parchear
    solo X.Y no afecta a los modulos que ya hicieron el import."""
    target_mod = sys.modules.get(module_name)
    if target_mod is None:
        return
    old_func = getattr(target_mod, func_name, None)
    if old_func is None:
        return
    setattr(target_mod, func_name, new_func)
    # Buscar otros modulos que tengan la misma referencia y patchearlos tambien
    for mod in list(sys.modules.values()):
        if mod is None or mod is target_mod:
            continue
        try:
            existing = getattr(mod, func_name, None)
        except Exception:
            continue
        if existing is old_func:
            try:
                setattr(mod, func_name, new_func)
            except Exception:
                pass


def _install_monkey_patches() -> None:
    """Sustituye las funciones de cdp_bridge que dependen de CDP en :9333
    por versiones que usan el inspector V8 + webContents.executeJavaScript.

    El resto del codigo del bot sigue funcionando SIN modificaciones.

    Las funciones patcheadas son AUTO-RECONECTANTES: si detectan que el WS
    al inspector murio (porque DICloak fue cerrado/reiniciado), gatillan
    un re-attach al PID nuevo y reintentan la llamada una vez. Asi el server
    se auto-repara cuando el usuario reinicia DICloak sin matar el server.
    """
    import cdp_bridge

    def _ensure_alive() -> bool:
        """Garantiza que el cliente inspector siga vivo. Si cayo, re-attach."""
        if _client is not None and _client.is_connected():
            return True
        log_warn("Inspector WS caido — intentando re-attach al DICloak actual")
        return try_attach_existing_dicloak(timeout=10.0)

    # ── cdp_evaluate_sync: el corazon del control. Todo flujo de CDP pasa por aqui.
    def patched_evaluate(expression, port=cdp_bridge.DEFAULT_DICLOAK_PORT, timeout=8):
        if not _ensure_alive():
            return None
        return _client.evaluate_in_renderer(expression, timeout=float(timeout))

    # ── inject_cdp_hook: el hook ya esta instalado al hacer attach, esto es idempotente.
    def patched_inject_hook(port=cdp_bridge.DEFAULT_DICLOAK_PORT):
        if not _ensure_alive():
            return False
        return _client.inject_hook(cdp_bridge.HOOK_JS)

    # ── is_dicloak_ready: chequea salud real del inspector, no solo el flag.
    def patched_is_ready(port=cdp_bridge.DEFAULT_DICLOAK_PORT):
        return _ensure_alive()

    # ── _get_page_ws_url: usado en main() para esperar que la pagina cargue.
    #    En modo attach ya tenemos acceso al renderer via inspector — devolvemos
    #    un sentinel no vacio para que el loop de espera continue.
    def patched_page_ws_url(port=cdp_bridge.DEFAULT_DICLOAK_PORT):
        return "inspector://attached" if _ensure_alive() else ""

    # ── init_cdp: en modo normal abre WS a :9333 e inyecta hook. En modo attach
    #    todo eso ya esta hecho — solo confirmamos que seguimos conectados.
    def patched_init_cdp(port=cdp_bridge.DEFAULT_DICLOAK_PORT):
        return _ensure_alive()

    _replace_function_globally("cdp_bridge", "cdp_evaluate_sync", patched_evaluate)
    _replace_function_globally("cdp_bridge", "inject_cdp_hook", patched_inject_hook)
    _replace_function_globally("cdp_bridge", "is_dicloak_ready", patched_is_ready)
    _replace_function_globally("cdp_bridge", "_get_page_ws_url", patched_page_ws_url)
    _replace_function_globally("cdp_bridge", "init_cdp", patched_init_cdp)

    log_ok("cdp_bridge monkey-patched para usar inspector V8 (modo ATTACH)")


# ── Entry point ──────────────────────────────────────────────────────────────

def try_attach_existing_dicloak(timeout: float = 15.0) -> bool:
    """Intenta enganchar a un DICloak corriendo manualmente sin debug.

    Flujo completo:
      1. Buscar PID del main process.
      2. Activar el inspector V8 con process._debugProcess.
      3. Esperar a que abra :9229.
      4. Conectarse via WebSocket.
      5. Inyectar HOOK_JS en el renderer via webContents.executeJavaScript.
      6. Monkey-patch cdp_bridge para usar el inspector como transporte.

    Retorna True si todo el flujo tuvo exito. False si falla en cualquier paso
    (entonces el caller debe usar el flujo normal de kill+relaunch).

    Idempotente: si ya hay un attach activo, retorna True sin hacer nada.
    """
    global _client, _active

    with _state_lock:
        # Idempotente: si ya hay attach activo Y el WS sigue vivo, salimos —
        # PERO solo si el PID conectado sigue siendo el DICloak activo.
        # Si el usuario cerro DICloak con la X (destruye ventana, proceso
        # sigue vivo en tray) y abrio una instancia nueva, el PID del main
        # con ventana cambio — tenemos que re-attach al nuevo.
        if _active and _client is not None:
            current_main_pid = find_dicloak_main_pid(wait_for_window=0.5)
            if _client.pid == current_main_pid and _client.is_connected():
                return True
            if _client.pid != current_main_pid:
                log_warn(
                    f"DICloak activo cambio: conectados a PID {_client.pid}, "
                    f"pero main con ventana ahora es PID {current_main_pid} — re-attach"
                )
            else:
                log_warn("Inspector WS caido (DICloak fue cerrado o reiniciado) — re-attach")
            try:
                _client.close()
            except Exception:
                pass
            _client = None
            _active = False

        # Limpiar DICloak zombis (mains sin ventana) antes de elegir PID.
        # Si el usuario cerro/reabrio DICloak varias veces, pueden quedar
        # procesos huerfanos que confundirian al picker y nos haria conectar
        # a un proceso muriendose.
        zombies = kill_dicloak_zombies()
        if zombies > 0:
            log_info(f"Limpiados {zombies} DICloak zombi(s) antes de attach")
            time.sleep(0.5)  # dejar que Windows libere los PIDs

        pid = find_dicloak_main_pid()
        if pid is None:
            return False

        log_info(
            f"DICloak corriendo sin debug detectado (PID {pid}) — "
            "intentando attach via inspector V8"
        )

        if not enable_v8_inspector(pid):
            return False

        if not wait_for_inspector(timeout=timeout):
            log_warn(f"Inspector V8 no respondio en :{DEFAULT_INSPECTOR_PORT} tras {timeout}s")
            return False

        ws_url = get_inspector_ws_url()
        if not ws_url:
            log_warn("No se pudo obtener WebSocket URL del inspector V8")
            return False

        client = InspectorClient(ws_url, pid=pid)
        if not client.connect():
            return False

        log_ok(f"Conectado al inspector V8 del main de DICloak: {ws_url[:70]}...")

        # Inyectar el hook reusando el HOOK_JS canonico de cdp_bridge
        try:
            from cdp_bridge import HOOK_JS
        except Exception as exc:
            log_error(f"No se pudo importar HOOK_JS de cdp_bridge: {exc}")
            client.close()
            return False

        if not client.inject_hook(HOOK_JS):
            log_warn("HOOK_JS no se pudo inyectar via inspector — abortando attach")
            client.close()
            return False

        log_ok("HOOK_JS inyectado en renderer de DICloak via inspector V8")

        _client = client
        _active = True

        _install_monkey_patches()

        return True


def detach() -> None:
    """Cierra la conexion al inspector y desactiva el modo attach.

    No revierte los monkey-patches (no es necesario para el cierre del server).
    """
    global _client, _active
    with _state_lock:
        if _client is not None:
            try:
                _client.close()
            except Exception:
                pass
        _client = None
        _active = False
