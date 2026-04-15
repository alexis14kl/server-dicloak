"""
DICloak API Server — Servidor REST standalone para controlar DICloak via CDP.

Uso:
  python server.py
  python server.py --port 8585 --dicloak-port 9333

Endpoints:
  GET  /                    → Info del servidor
  GET  /health              → Estado de DICloak
  GET  /profiles            → Lista perfiles
  GET  /profiles/running    → Perfiles abiertos + puertos CDP
  POST /profiles/open       → Abrir perfil por nombre
  POST /profiles/close      → Cerrar perfil
  POST /profiles/hook       → Inyectar hook CDP
"""
from __future__ import annotations

import json as _json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any

# ── Project setup ─────────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

# Cargar .env si existe
env_path = PROJECT_ROOT / ".env"
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator
import uvicorn

from cdp_bridge import (
    is_dicloak_ready,
    get_dicloak_targets,
    list_profiles_via_cdp,
    inject_cdp_hook,
    open_profile_via_cdp,
    detect_ginsbrowser_port,
    _test_cdp_port,
    _get_page_ws_url,
    init_cdp,
    cdp_evaluate_sync,
    _ensure_on_profile_list,
    DEFAULT_DICLOAK_PORT,
)
from platform_utils import (
    find_dicloak_exe,
    launch_detached,
    get_process_list,
    get_browser_process_name,
    read_cdp_debug_info,
    write_cdp_debug_info,
)
try:
    from api import DICloakAPI
except ImportError:
    DICloakAPI = None
from logger import log_info, log_ok, log_warn

# ── Config ────────────────────────────────────────────────────────────────────

SERVER_PORT = int(os.environ.get("DICLOAK_API_PORT", "0") or "0") or 8585
DICLOAK_PORT = int(os.environ.get("CDP_DICLOAK_PORT", "0") or "0") or DEFAULT_DICLOAK_PORT
PUBLIC_BASE_URL = (os.environ.get("PUBLIC_BASE_URL", "") or f"http://127.0.0.1:{SERVER_PORT}").rstrip("/")
IMAGES_DIR = PROJECT_ROOT / "output" / "images"
IMAGES_DIR.mkdir(parents=True, exist_ok=True)

# ── Response helpers ──────────────────────────────────────────────────────────

def success_response(data: Any = None, message: str = "OK") -> Response:
    body = {"success": True, "message": message}
    if data is not None:
        body["data"] = data
    return Response(
        content=_json.dumps(body, indent=2, ensure_ascii=False),
        status_code=200,
        media_type="application/json",
    )


def error_response(message: str, status_code: int = 500, details: Any = None) -> Response:
    body = {"success": False, "error": message}
    if details is not None:
        body["details"] = details
    return Response(
        content=_json.dumps(body, indent=2, ensure_ascii=False),
        status_code=status_code,
        media_type="application/json",
    )


def public_url(path: str) -> str:
    return f"{PUBLIC_BASE_URL}/{path.lstrip('/')}"


# ── Service layer ─────────────────────────────────────────────────────────────

class DICloakService:
    # Lock CORTO que solo protege el click en la UI de DICloak (una sola
    # ventana, dos clicks simultaneos se pisarian). ~50-200ms.
    _click_lock: threading.Lock = threading.Lock()

    # Set de puertos "en reclamacion" por aperturas concurrentes. Cada
    # caller que detecta un puerto nuevo lo agrega atomicamente aqui, y
    # los demas callers lo excluyen de su busqueda — asi dos aperturas
    # paralelas no se roban el puerto mutuamente sin necesidad de
    # sostener el _click_lock durante la espera larga del CDP.
    _claim_lock: threading.Lock = threading.Lock()
    _claimed_ports: set[int] = set()

    def __init__(self, dicloak_port: int = DEFAULT_DICLOAK_PORT):
        self.port = dicloak_port
        self._openapi_available: bool | None = None  # instancia, no clase

    def check_health(self) -> dict:
        ready = is_dicloak_ready(self.port)
        targets = get_dicloak_targets(self.port) if ready else []
        return {
            "dicloak_cdp_port": self.port,
            "dicloak_ready": ready,
            "targets_count": len(targets),
        }

    def get_profiles(self) -> list[dict]:
        # Intentar REST API solo si no ha fallado antes (evita ~10s de timeouts)
        if self._openapi_available is not False:
            try:
                if DICloakAPI is None:
                    raise ImportError("api.py no disponible")
                api = DICloakAPI()
                if self._openapi_available is None:
                    self._openapi_available = api.is_available()
                if self._openapi_available:
                    profiles = api.list_profiles()
                    if profiles:
                        return [{"id": p.id, "name": p.name, "status": p.status} for p in profiles]
            except Exception:
                self._openapi_available = False

        # CDP directo — lee la tabla del DOM en ~6ms
        if not is_dicloak_ready(self.port):
            raise ConnectionError("DICloak no responde en puerto CDP.")
        profiles = list_profiles_via_cdp(self.port)
        return [{"id": p.id, "name": p.name, "status": p.status} for p in profiles]

    def get_running_profiles(self) -> list[dict]:
        browser_name = get_browser_process_name().lower()
        procs = get_process_list()
        running = []
        for p in procs:
            name = str(p.get("name", "")).lower()
            cmd = str(p.get("cmdline", ""))
            if name != browser_name and "ginsbrowser" not in cmd.lower():
                continue
            if "--type=" in cmd:
                continue
            m = re.search(r"--remote-debugging-port[=\s](\d{2,5})", cmd)
            if not m:
                continue
            port = int(m.group(1))
            running.append({
                "pid": p.get("pid", 0),
                "debug_port": port,
                "cdp_active": _test_cdp_port(port),
            })
        return running

    def _snapshot_active_ports(self) -> set[int]:
        """Captura el set de puertos CDP activos en este instante.

        Se usa ANTES de clickear un nuevo perfil para poder luego
        diferenciar cual es el puerto nuevo que aparecio (el del perfil
        recien abierto), permitiendo aperturas en paralelo.

        Ademas limpia _claimed_ports de puertos ya muertos (perfiles
        cerrados) para evitar que el set crezca sin limite.
        """
        ports: set[int] = set()
        data = read_cdp_debug_info()
        for entry in data.values():
            if not isinstance(entry, dict):
                continue
            try:
                p = int(entry.get("debugPort") or entry.get("port") or 0)
            except (TypeError, ValueError):
                continue
            if p and _test_cdp_port(p):
                ports.add(p)
        for prof in self.get_running_profiles():
            rp = int(prof.get("debug_port", 0) or 0)
            if rp and prof.get("cdp_active"):
                ports.add(rp)

        # Limpieza: quitar de _claimed_ports los que ya no estan vivos.
        with self._claim_lock:
            dead = self._claimed_ports - ports
            if dead:
                self._claimed_ports -= dead
                log_info(f"Claimed ports limpiados (perfiles cerrados): {sorted(dead)}")

        return ports

    def open_profile(self, name: str, timeout: int = 60) -> dict:
        if not name:
            raise ValueError("El nombre del perfil es requerido.")
        if not is_dicloak_ready(self.port):
            raise ConnectionError("DICloak no responde. Verifica que este abierto.")

        # Estrategia 1: Si el perfil solicitado YA esta asociado a un puerto
        # vivo en cdp_debug_info.json, reutilizar directo. Sin lock.
        # NOTA: esto retorna el primer puerto encontrado en el archivo, lo
        # cual era el bug anterior — pero solo se toma este path cuando ya
        # hay un puerto vivo. Para paralelismo real, dos perfiles nuevos
        # distintos pasaran de largo esta estrategia (no estan en el json).
        data = read_cdp_debug_info()
        for entry in data.values():
            if not isinstance(entry, dict):
                continue
            entry_name = str(entry.get("profileName") or entry.get("name") or "").strip()
            if entry_name and entry_name != name:
                continue  # Este port pertenece a otro perfil
            try:
                port = int(entry.get("debugPort") or entry.get("port") or 0)
            except (TypeError, ValueError):
                continue
            if port and _test_cdp_port(port):
                return {
                    "name": name,
                    "debug_port": port,
                    "ws_url": str(entry.get("webSocketUrl") or ""),
                    "cdp_active": True,
                }

        # Snapshot de puertos activos ANTES de entrar al click lock.
        # El lock solo protege el click (~50-200ms), la espera larga por
        # el CDP ocurre fuera para no bloquear otras aperturas.
        known_ports = self._snapshot_active_ports()
        # Excluir tambien puertos que otros threads ya reclamaron para si.
        with self._claim_lock:
            exclude_set = known_ports | set(self._claimed_ports)
        log_info(f"Snapshot puertos antes de abrir '{name}': known={sorted(known_ports)} claimed={sorted(self._claimed_ports)}")

        with self._click_lock:
            status = open_profile_via_cdp(name, self.port)

        if status == "PROFILE_NOT_FOUND":
            available = [p.name for p in list_profiles_via_cdp(self.port)]
            raise FileNotFoundError(
                f"Perfil '{name}' no encontrado. Disponibles: {available}"
            )

        # Espera fuera del lock. _wait_for_cdp_port reclamara atomicamente
        # el primer puerto nuevo que encuentre (agregandolo a _claimed_ports)
        # para que aperturas paralelas no lo tomen.
        claimed_port = self._wait_and_claim_port(timeout, exclude_set)

        if status == "ALREADY_OPEN":
            log_info(f"Perfil '{name}' ya abierto — buscando CDP...")
            if claimed_port:
                return {"name": name, "debug_port": claimed_port, "ws_url": "",
                        "cdp_active": True}
            log_warn("Perfil abierto sin CDP — reinyectando hook...")
            try:
                inject_cdp_hook(self.port)
                time.sleep(3)
            except Exception:
                pass
            port = self._wait_and_claim_port(min(timeout, 20), exclude_set)
            if port:
                return {"name": name, "debug_port": port, "ws_url": "",
                        "cdp_active": True}
            return {"name": name, "debug_port": 0, "ws_url": "",
                    "cdp_active": False, "clicked": False}

        # CLICKED_OPEN — ya tenemos claimed_port
        if claimed_port:
            return {"name": name, "debug_port": claimed_port, "ws_url": "",
                    "cdp_active": True, "clicked": True}

        # Sin CDP tras abrir: reintento con reinyeccion.
        log_warn("CDP no disponible tras abrir — reinyectando hook y reabriendo...")
        try:
            inject_cdp_hook(self.port)
            time.sleep(2)
        except Exception:
            pass

        known_ports_2 = self._snapshot_active_ports()
        with self._claim_lock:
            exclude_set_2 = known_ports_2 | set(self._claimed_ports)
        with self._click_lock:
            status2 = open_profile_via_cdp(name, self.port)
        if status2 in ("CLICKED_OPEN", "ALREADY_OPEN"):
            port2 = self._wait_and_claim_port(timeout, exclude_set_2)
            if port2:
                log_ok(f"CDP activo tras reinyeccion — puerto {port2}")
                return {"name": name, "debug_port": port2, "ws_url": "",
                        "cdp_active": True, "clicked": True}

        return {"name": name, "debug_port": 0, "ws_url": "",
                "cdp_active": False, "clicked": True}

    def _wait_and_claim_port(self, timeout: int, exclude: set[int]) -> int:
        """Espera y RECLAMA atomicamente un puerto CDP nuevo.

        Igual que _wait_for_cdp_port pero ademas agrega el puerto a
        self._claimed_ports bajo self._claim_lock antes de retornarlo.
        Si el puerto detectado ya fue reclamado por otro thread (carrera),
        lo ignora y sigue polleando.
        """
        deadline = time.time() + timeout
        attempt = 0
        while time.time() < deadline:
            attempt += 1
            candidates: list[int] = []

            data = read_cdp_debug_info()
            for entry in data.values():
                if not isinstance(entry, dict):
                    continue
                try:
                    port = int(entry.get("debugPort") or entry.get("port") or 0)
                except (TypeError, ValueError):
                    continue
                if port and port not in exclude and _test_cdp_port(port):
                    candidates.append(port)

            for prof in self.get_running_profiles():
                rport = int(prof.get("debug_port", 0) or 0)
                if rport and rport not in exclude and prof.get("cdp_active"):
                    if rport not in candidates:
                        candidates.append(rport)

            # Intentar reclamar atomicamente el primer candidato no tomado
            if candidates:
                with self._claim_lock:
                    for port in candidates:
                        if port not in self._claimed_ports:
                            self._claimed_ports.add(port)
                            log_ok(f"CDP RECLAMADO — puerto {port} (intento {attempt}, claimed={sorted(self._claimed_ports)})")
                            return port
                # Todos los candidatos ya reclamados por otros threads;
                # agregar al exclude local y seguir esperando.
                exclude = exclude | set(candidates)

            elapsed = timeout - (deadline - time.time())
            time.sleep(0.3 if elapsed < 10 else 1)

        log_warn(f"CDP no detectado tras {timeout}s ({attempt} intentos, exclude={sorted(exclude)}, claimed={sorted(self._claimed_ports)})")
        return 0

    def _wait_for_cdp_port(self, timeout: int, exclude: set[int] | None = None) -> int:
        """Espera a que aparezca un puerto CDP activo NUEVO. Retorna puerto o 0.

        Args:
            timeout: segundos a esperar.
            exclude: set de puertos ya conocidos (snapshot antes del click).
                Solo se retornan puertos que NO esten en este set — asi dos
                llamadas concurrentes a open_profile pueden detectar cada una
                su propio puerto recien creado sin pisarse.
        """
        exclude = exclude or set()
        deadline = time.time() + timeout
        attempt = 0
        while time.time() < deadline:
            attempt += 1
            # Via cdp_debug_info.json (rápido, archivo local)
            data = read_cdp_debug_info()
            for entry in data.values():
                if not isinstance(entry, dict):
                    continue
                try:
                    port = int(entry.get("debugPort") or entry.get("port") or 0)
                except (TypeError, ValueError):
                    continue
                if port and port not in exclude and _test_cdp_port(port):
                    log_ok(f"CDP NUEVO via cdp_debug_info — puerto {port} (intento {attempt})")
                    return port
            # Via proceso (fallback — busca ginsbrowser con --remote-debugging-port)
            running = self.get_running_profiles()
            for p in running:
                rport = int(p.get("debug_port", 0) or 0)
                if rport and rport not in exclude and p.get("cdp_active"):
                    log_ok(f"CDP NUEVO via proceso — puerto {rport} (intento {attempt})")
                    return rport
            # Polling agresivo los primeros 10s, luego más relajado
            elapsed = timeout - (deadline - time.time())
            time.sleep(0.3 if elapsed < 10 else 1)
        log_warn(f"CDP no detectado tras {timeout}s de polling ({attempt} intentos, exclude={sorted(exclude)})")
        return 0

    def close_profiles(self) -> int:
        try:
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/F", "/IM", "ginsbrowser.exe"],
                               capture_output=True, timeout=5)
            else:
                subprocess.run(["pkill", "-f", "ginsbrowser"],
                               capture_output=True, timeout=5)
            return 1
        except Exception:
            return 0

    def inject_hook(self) -> bool:
        if not is_dicloak_ready(self.port):
            raise ConnectionError("DICloak no responde.")
        return inject_cdp_hook(self.port)


# ── Request models ────────────────────────────────────────────────────────────

class OpenProfileRequest(BaseModel):
    name: str
    timeout: int = 60

class CloseProfileRequest(BaseModel):
    name: str = ""
    id: str = ""

class ChatGPTStabilizeRequest(BaseModel):
    port: int
    timeout: int = 30

class PromptRequest(BaseModel):
    port: int
    prompt: str
    wait_response: bool = False
    timeout: int = 120
    auto_rotate: bool = True
    paste_only: bool = False
    new_conversation: bool = False  # Si True, navega a chatgpt.com antes del prompt (nueva sesión)

    @field_validator("prompt")
    @classmethod
    def prompt_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("El prompt no puede estar vacío")
        return v.strip()

class SendPastedPromptRequest(BaseModel):
    port: int
    wait_response: bool = False
    timeout: int = 120
    target_ws: str = ""

class ImageDownloadRequest(BaseModel):
    port: int
    output_dir: str = ""
    timeout: int = 300
    webhook_url: str = ""
    job_id: str = ""
    target_ws: str = ""

class Veo3StabilizeRequest(BaseModel):
    port: int
    timeout: int = 60

class Veo3ExtendVideoRequest(BaseModel):
    port: int
    prompt: str

class Veo3DownloadVideoRequest(BaseModel):
    port: int
    timeout: int = 70
    output_dir: str = ""
    webhook_url: str = ""
    job_id: str = ""


# ── Auto-launch ──────────────────────────────────────────────────────────────

def ensure_dicloak_running(port: int = DEFAULT_DICLOAK_PORT, timeout: int = 20) -> bool:
    if is_dicloak_ready(port):
        print(f"[OK] DICloak ya responde en puerto {port}")
        return True

    dicloak_exe = find_dicloak_exe()
    if not dicloak_exe:
        print("[ERROR] DICloak no encontrado en el sistema.")
        return False

    print(f"[INFO] Abriendo DICloak en modo depuracion (puerto {port})...")
    launch_cmd = f'"{dicloak_exe}" --remote-debugging-port={port} --remote-allow-origins=*'
    launch_detached(launch_cmd)

    deadline = time.time() + timeout
    while time.time() < deadline:
        if is_dicloak_ready(port):
            print(f"[OK] DICloak listo en puerto {port}")
            return True
        time.sleep(1)

    print(f"[WARN] DICloak no respondio en {timeout}s.")
    return False


# ── App ───────────────────────────────────────────────────────────────────────

from fastapi.staticfiles import StaticFiles


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Hook de ciclo de vida. En shutdown notifica a los loops cooperativos
    para que aborten sin esperar al timeout de uvicorn."""
    yield
    # Uvicorn ya recibio Ctrl+C y esta cerrando — marcar el shutdown event
    # para que los loops largos (image_download fase 2, polling de veo3, etc.)
    # rompan en el siguiente checkpoint en vez de quedar bloqueados.
    try:
        from cancellation import request_shutdown
        request_shutdown()
        log_warn("[shutdown] shutdown event marcado — loops cooperativos abortando")
    except Exception:
        pass
    try:
        from cdp_bridge import close_cdp
        close_cdp()
    except Exception:
        pass


app = FastAPI(title="DICloak Control API", version="1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# Servir archivos descargados (imagenes y videos)
app.mount("/files/images", StaticFiles(directory=str(IMAGES_DIR)), name="images")

OUTPUT_DIR = PROJECT_ROOT / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/files/output", StaticFiles(directory=str(OUTPUT_DIR)), name="output")

service = DICloakService(DICLOAK_PORT)


# ── Pool routing helper ──────────────────────────────────────────────────────
# Resuelve el puerto CDP correcto segun el pool del endpoint (image / video).
#
# El orchestrator (Publicidad/Django) mantiene el flujo actual:
#   1. POST /profiles/open {"name": "..."}     → DICloak abre el perfil y devuelve port
#   2. POST /chatgpt/stabilize  {"port": ...}  → estabiliza la sesion ChatGPT
#   3. POST /chatgpt/prompt     {"port": ...}  → pega prompt
#   4. POST /chatgpt/download-image            → espera y descarga imagen
#   (con rotacion de cuentas dentro del mismo perfil cuando hay rate limit)
#
# Reglas de resolucion:
#   1. Puerto del cliente vivo + clasificado al pool correcto → usar tal cual.
#   2. Puerto vivo pero SIN clasificar (perfil recien abierto, homepage aun cargando,
#      o redirigiendo entre paginas de auth) → CONFIAR en el cliente, no romper
#      el flujo. El lock del pool sigue protegiendo concurrencia.
#   3. Puerto vivo pero del pool EQUIVOCADO → redirigir al pool correcto via
#      descubrimiento dinamico (proteccion contra cross-talk).
#   4. Puerto muerto/vacio → resolver dinamicamente desde los perfiles corriendo.
#   5. Sin candidatos → 503 con mensaje claro.
def resolve_pool_port(client_port: int, pool: str) -> tuple[int, str]:
    """Devuelve (puerto, error). Si error != "", el endpoint debe abortar."""
    from profile_pool import classify_port, resolve_port

    if client_port and _test_cdp_port(client_port):
        actual = classify_port(client_port)
        if actual == pool:
            return client_port, ""
        if actual is None:
            # Perfil recien abierto via /profiles/open: el navegador aun esta
            # cargando la homepage del perfil (chatgpt.com o labs.google). En
            # este momento /json todavia no tiene una URL reconocible. NO
            # romper el flujo — el orchestrator sabe que perfil abrio y para
            # que. El lock del pool sigue serializando correctamente.
            log_info(f"[pool/{pool}] puerto {client_port} sin pestaña reconocible aun — confiando en el cliente")
            return client_port, ""
        # Pool equivocado: el orchestrator mando un puerto que clasifica a
        # otro pool. Redirigir dinamicamente para evitar cross-talk.
        log_warn(f"[pool/{pool}] puerto {client_port} pertenece al pool '{actual}' — redirigiendo")

    running = service.get_running_profiles()
    resolved = resolve_port(pool, running)
    if resolved:
        if resolved != client_port:
            log_ok(f"[pool/{pool}] puerto resuelto dinamicamente: {resolved}")
        return resolved, ""
    return 0, f"No hay perfil activo para el pool '{pool}' (orchestrator debe llamar /profiles/open primero)"


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/")
def index():
    return success_response(data={
        "name": "DICloak Control API",
        "version": "1.0",
        "dicloak_port": DICLOAK_PORT,
        "server_port": SERVER_PORT,
        "endpoints": [
            "GET  /health",
            "GET  /profiles",
            "GET  /profiles/search/{name}",
            "GET  /profiles/running",
            "POST /profiles/open",
            "POST /profiles/close",
            "POST /profiles/hook",
            "POST /chatgpt/stabilize",
            "POST /chatgpt/prompt",
            "POST /chatgpt/send-pasted",
            "POST /chatgpt/download-image",
            "POST /veo3/stabilize",
            "POST /veo3/new-project",
            "POST /veo3/extend-video",
            "POST /veo3/download-video",
        ],
    })

@app.get("/health")
def health():
    try:
        data = service.check_health()
        status = "ok" if data["dicloak_ready"] else "dicloak_not_found"
        return success_response(data=data, message=status)
    except Exception as e:
        return error_response(str(e), 500)


# ─── Cancelacion cooperativa de jobs ──────────────────────────────────────
# Publicidad llama POST /jobs/{job_id}/cancel para indicar que debe
# abortar la operacion activa asociada a ese job. El handler solo flipea
# una bandera en el registry en memoria — los loops internos (wait,
# paste, download) son quienes realmente la consultan entre iteraciones.
#
# Respuesta inmediata (no bloquea esperando el abort real). La latencia
# entre el flip de la bandera y el aborto efectivo es de 1-3s (el tiempo
# del siguiente checkpoint en los loops). Esto es aceptable y documentado.
@app.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str):
    try:
        from cancellation import mark_cancelled, list_cancelled

        added = mark_cancelled(job_id)
        if added:
            log_info(f"[cancel] Job {job_id} marcado para abortar (pendientes={len(list_cancelled())})")
            return success_response(
                data={"job_id": job_id, "already_marked": False},
                message="Job marcado para cancelacion",
            )
        log_info(f"[cancel] Job {job_id} ya estaba marcado")
        return success_response(
            data={"job_id": job_id, "already_marked": True},
            message="Job ya estaba marcado previamente",
        )
    except Exception as e:
        log_warn(f"[cancel] Error marcando job {job_id}: {e}")
        return error_response(str(e), 500)

@app.get("/profiles/search/{name}")
def search_profile(name: str):
    try:
        profiles = service.get_profiles()
        target = name.lower().strip()
        target_words = target.split()
        matches = [p for p in profiles if all(w in p["name"].lower() for w in target_words)]
        if matches:
            return success_response(
                data={"count": len(matches), "profiles": matches},
                message=f"{len(matches)} perfil(es) encontrado(s)",
            )
        return error_response(f"Perfil '{name}' no encontrado", 404,
                              details={"available": [p["name"] for p in profiles]})
    except ConnectionError as e:
        return error_response(str(e), 503)
    except Exception as e:
        return error_response(str(e), 500)

@app.get("/profiles")
def list_profiles():
    try:
        profiles = service.get_profiles()
        return success_response(data={"count": len(profiles), "profiles": profiles})
    except ConnectionError as e:
        return error_response(str(e), 503)
    except Exception as e:
        return error_response(str(e), 500)

@app.get("/profiles/running")
def running_profiles():
    try:
        running = service.get_running_profiles()
        return success_response(data={"count": len(running), "profiles": running})
    except Exception as e:
        return error_response(str(e), 500)


@app.get("/pools")
def pools_status():
    """Estado del routing por pool (debug). Muestra que perfiles activos
    estan en cada pool y que locks estan ocupados ahora mismo.

    Util para verificar que el orchestrator abrio los perfiles correctos
    (uno con chatgpt.com, otro con labs.google) y que las peticiones
    concurrentes estan tomando los locks que esperabamos.
    """
    try:
        from profile_pool import (
            discover_pools, classify_port, _pool_locks, known_pools,
            POOL_URL_PATTERNS,
        )
        running = service.get_running_profiles()
        mapping = discover_pools(running, force=True)

        # Detalle por puerto: incluir si el lock del pool esta tomado.
        # acquire(blocking=False) es no destructivo: si el lock esta libre lo
        # toma momentaneamente y lo suelta inmediatamente; si esta tomado
        # devuelve False sin bloquear.
        pools_info = {}
        for pool_name in known_pools():
            lock = _pool_locks[pool_name]
            acquired = lock.acquire(blocking=False)
            if acquired:
                lock.release()
                lock_busy = False
            else:
                lock_busy = True
            pools_info[pool_name] = {
                "ports": mapping.get(pool_name, []),
                "lock_busy": lock_busy,
                "url_patterns": list(POOL_URL_PATTERNS[pool_name]),
            }

        # Detalle por puerto running: que pool quedo asignado (o None)
        running_detail = []
        for prof in running:
            port = prof.get("debug_port", 0)
            running_detail.append({
                "pid": prof.get("pid", 0),
                "debug_port": port,
                "cdp_active": prof.get("cdp_active", False),
                "classified_pool": classify_port(port) if prof.get("cdp_active") else None,
            })

        return success_response(data={
            "pools": pools_info,
            "running_profiles": running_detail,
            "total_running": len(running),
        }, message="Estado de pools")
    except Exception as e:
        return error_response(str(e), 500)

@app.post("/profiles/open")
def open_profile(req: OpenProfileRequest):
    try:
        profile = service.open_profile(req.name, req.timeout)
        return success_response(data={"profile": profile}, message=f"Perfil '{req.name}' abierto")
    except ValueError as e:
        return error_response(str(e), 400)
    except ConnectionError as e:
        return error_response(str(e), 503)
    except FileNotFoundError as e:
        return error_response(str(e), 404)
    except TimeoutError as e:
        return error_response(str(e), 408)
    except Exception as e:
        return error_response(str(e), 500)

@app.post("/profiles/close")
def close_profiles(req: CloseProfileRequest = None):
    try:
        killed = service.close_profiles()
        return success_response(
            data={"killed_processes": killed},
            message=f"{killed} proceso(s) cerrado(s)" if killed else "No habia perfiles abiertos"
        )
    except Exception as e:
        return error_response(str(e), 500)

@app.post("/profiles/hook")
def inject_hook():
    try:
        ok = service.inject_hook()
        if ok:
            return success_response(message="Hook CDP inyectado correctamente")
        return error_response("No se pudo inyectar el hook CDP", 500)
    except ConnectionError as e:
        return error_response(str(e), 503)
    except Exception as e:
        return error_response(str(e), 500)


@app.post("/chatgpt/stabilize")
def chatgpt_stabilize(req: ChatGPTStabilizeRequest):
    """Estabiliza ChatGPT: cierra tabs duplicadas, deja 1 sola lista para generar."""
    from profile_pool import acquire_port_lock, acquire_pool_semaphore
    try:
        port, err = resolve_pool_port(req.port, "image")
        if err:
            return error_response(err, 503)

        with acquire_pool_semaphore("image"), acquire_port_lock(port):
            from chat_gpt_consulta.stabilize import stabilize_chatgpt
            result = stabilize_chatgpt(port=port, timeout=req.timeout)
        if result.get("success"):
            return success_response(data=result, message=result.get("message", "ChatGPT estabilizado"))
        return error_response(result.get("error", "Error estabilizando"), 500, details=result)
    except Exception as e:
        return error_response(str(e), 500)


@app.post("/chatgpt/prompt")
def chatgpt_prompt(req: PromptRequest):
    from profile_pool import acquire_port_lock, acquire_pool_semaphore
    try:
        port, err = resolve_pool_port(req.port, "image")
        if err:
            return error_response(err, 503)

        # Lock del pool image — serializa peticiones del flujo de imagenes,
        # pero no bloquea peticiones del pool video (que corren en paralelo).
        with acquire_pool_semaphore("image"), acquire_port_lock(port):
            return _chatgpt_prompt_locked(req, port)
    except Exception as e:
        return error_response(str(e), 500)


def _chatgpt_prompt_locked(req: PromptRequest, port: int):
    try:
        # Verificar proxy y crear tab sin proxy si es necesario
        from chat_gpt_consulta.proxy_bypass import ensure_chatgpt_reachable
        port, tab_ws = ensure_chatgpt_reachable(port)
        if not port:
            return error_response("Proxy muerto y no se pudo crear bypass", 503)

        # Estabilizar: cerrar tabs duplicadas DESPUES de proxy_bypass
        # Proteger la tab que proxy_bypass creó (tab_ws) — cerrar las demás
        try:
            from chat_gpt_consulta.stabilize import stabilize_chatgpt
            # Extraer el targetId de tab_ws para protegerla
            keep_id = ""
            if tab_ws:
                # ws URL format: .../devtools/page/TARGET_ID
                keep_id = tab_ws.rsplit("/", 1)[-1] if "/page/" in tab_ws else ""
            stab = stabilize_chatgpt(port=port, timeout=15, keep_target_id=keep_id)
            closed = stab.get("tabs_closed", 0)
            if closed:
                log_info(f"Stabilize: {closed} tabs cerradas antes del prompt")
        except Exception as e:
            log_warn(f"Stabilize fallo (no critico): {e}")

        # Si es una nueva sesión (no corrección), hacer click en el botón "Nuevo chat"
        # de ChatGPT para iniciar una conversación limpia sin mezclar contextos.
        # Se usa CDP sobre la tab activa — no se navega ni se recarga la página.
        if req.new_conversation:
            try:
                import time as _time
                from chat_gpt_consulta.prompt_paste import ChatGPTSession
                import websockets.sync.client as _ws_sync

                _nc_session = ChatGPTSession(port=port)
                if tab_ws:
                    _nc_session.ws_url = tab_ws
                    _nc_session._ws = _ws_sync.connect(tab_ws, max_size=2**22)
                else:
                    _nc_session.connect()

                clicked = _nc_session.evaluate("""(() => {
                    const btn = document.querySelector('[data-testid="create-new-chat-button"]')
                        || document.querySelector('a[href="/"]')
                        || document.querySelector('button[aria-label="New chat"]')
                        || document.querySelector('button[aria-label="Nuevo chat"]');
                    if (btn) { btn.click(); return true; }
                    return false;
                })()""")

                if clicked:
                    log_info("new_conversation: click en 'Nuevo chat' realizado")
                    _time.sleep(3)  # Esperar que el DOM del chat vacío cargue
                else:
                    log_warn("new_conversation: no se encontró botón 'Nuevo chat', continuando igual")

                _nc_session.close()
                tab_ws = ""  # Reconectar a la tab actualizada tras el click
            except Exception as e:
                log_warn(f"new_conversation: fallo al crear nuevo chat (no critico): {e}")

        if req.paste_only:
            from chat_gpt_consulta.prompt_paste import paste_prompt_only
            result = paste_prompt_only(
                port=port,
                prompt=req.prompt,
                target_ws=tab_ws,
            )
        elif req.auto_rotate:
            from chat_gpt_consulta.prompt_paste import paste_and_send_with_rotation
            result = paste_and_send_with_rotation(
                port=port,
                prompt=req.prompt,
                wait_response=req.wait_response,
                timeout=req.timeout,
                target_ws=tab_ws,
            )
        else:
            from chat_gpt_consulta.prompt_paste import paste_and_send_prompt
            result = paste_and_send_prompt(
                port=port,
                prompt=req.prompt,
                wait_response=req.wait_response,
                timeout=req.timeout,
                target_ws=tab_ws,
            )
        if result.get("success"):
            message = "Prompt pegado en ChatGPT" if req.paste_only else "Prompt enviado a ChatGPT"
            return success_response(data=result, message=message)
        status_code = 429 if result.get("error") == "rate_limited" else 500
        return error_response(result.get("error", "Error desconocido"), status_code, details=result)
    except Exception as e:
        return error_response(str(e), 500)


@app.post("/chatgpt/send-pasted")
def chatgpt_send_pasted(req: SendPastedPromptRequest):
    from profile_pool import acquire_port_lock, acquire_pool_semaphore
    try:
        port, err = resolve_pool_port(req.port, "image")
        if err:
            return error_response(err, 503)
        with acquire_pool_semaphore("image"), acquire_port_lock(port):
            from chat_gpt_consulta.prompt_paste import send_pasted_prompt
            result = send_pasted_prompt(
                port=port,
                wait_response=req.wait_response,
                timeout=req.timeout,
                target_ws=req.target_ws,
            )
        if result.get("success"):
            return success_response(data=result, message="Prompt pegado enviado a ChatGPT")
        status_code = 429 if result.get("error") == "rate_limited" else 500
        return error_response(result.get("error", "Error desconocido"), status_code, details=result)
    except Exception as e:
        return error_response(str(e), 500)


@app.post("/veo3/stabilize")
def veo3_stabilize(req: Veo3StabilizeRequest):
    from profile_pool import acquire_port_lock, acquire_pool_semaphore
    try:
        port, err = resolve_pool_port(req.port, "video")
        if err:
            return error_response(err, 503)
        with acquire_pool_semaphore("video"), acquire_port_lock(port):
            from chat_veo3_videos.veo3_session import navigate_and_stabilize
            result = navigate_and_stabilize(port=port, timeout=req.timeout)
        if result.get("success"):
            return success_response(data=result, message="Veo 3 estable y listo")
        return error_response(result.get("error", "Error desconocido"), 500, details=result.get("details"))
    except Exception as e:
        return error_response(str(e), 500)

class Veo3NewProjectRequest(BaseModel):
    port: int
    prompt: str = ""

@app.post("/veo3/new-project")
def veo3_new_project(req: Veo3NewProjectRequest):
    from profile_pool import acquire_port_lock, acquire_pool_semaphore
    try:
        port, err = resolve_pool_port(req.port, "video")
        if err:
            return error_response(err, 503)
        with acquire_pool_semaphore("video"), acquire_port_lock(port):
            from chat_veo3_videos.veo3_session import open_new_project
            result = open_new_project(port=port, prompt=req.prompt)
        if result.get("success"):
            return success_response(data=result, message="Nuevo proyecto abierto en Flow")
        # Propagar error clasificado en 'details' para que el cliente Python
        # pueda leer el code (page_load_timeout, account_chooser_no_accounts,
        # password_input_not_found, password_not_saved, next_button_not_found,
        # google_flow_redirect_timeout, login_state_unknown, etc.)
        return error_response(
            result.get("error", "Error desconocido"),
            500,
            details={"error": result.get("error", ""), **result},
        )
    except Exception as e:
        return error_response(str(e), 500)

@app.post("/veo3/download-video")
def veo3_download_video(req: Veo3DownloadVideoRequest):
    from profile_pool import acquire_port_lock, acquire_pool_semaphore
    try:
        port, err = resolve_pool_port(req.port, "video")
        if err:
            return error_response(err, 503)
        with acquire_pool_semaphore("video"), acquire_port_lock(port):
            from chat_veo3_videos.exten_video import download_extended_video
            result = download_extended_video(
                port=port,
                timeout=req.timeout,
                output_dir=req.output_dir,
            )
        if result.get("success"):
            # Construir video_url HTTP servible (mismo patron que imagen)
            file_name = result.get("file_name", "")
            if file_name:
                result["video_url"] = public_url(f"/files/output/videos/{file_name}")
            _notify_video_webhook(req.webhook_url, req.job_id, result)
            return success_response(data=result, message="Video descargado")
        return error_response(result.get("error", "Error desconocido"), 500, details=result)
    except Exception as e:
        return error_response(str(e), 500)

@app.post("/chatgpt/download-image")
def chatgpt_download_image(req: ImageDownloadRequest):
    # Se propaga req.job_id al flujo para que los loops internos puedan
    # consultar el registry de cancelacion y abortar cooperativamente.
    # Al terminar (en cualquier branch), limpiamos la bandera del registry
    # para que el set no crezca indefinidamente entre ejecuciones.
    from cancellation import clear as clear_cancel_flag, JobCancelled
    from profile_pool import acquire_port_lock, acquire_pool_semaphore
    try:
        port, err = resolve_pool_port(req.port, "image")
        if err:
            return error_response(err, 503)

        with acquire_pool_semaphore("image"), acquire_port_lock(port):
            from chat_gpt_consulta.image_download import wait_and_download_image
            # Siempre guardar en el directorio propio del servidor
            result = wait_and_download_image(
                port=port,
                output_dir=str(IMAGES_DIR),
                timeout=req.timeout,
                target_ws=req.target_ws,
                job_id=req.job_id,
            )
        if result.get("success"):
            # Agregar URL HTTP servible por este servidor
            file_name = result.get("file_name", "")
            if file_name:
                result["image_url"] = public_url(f"/files/images/{file_name}")
            _notify_webhook(req.webhook_url, req.job_id, result)
            return success_response(data=result, message="Imagen descargada")
        # Si el flujo detecto cancelacion, lo reflejamos con status 499
        # (codigo "client closed request") para que Publicidad lo distinga
        # de un error real. El webhook NO se dispara para cancelados.
        if result.get("error") == "cancelled":
            log_info(f"[cancel] download-image cancelado limpiamente (job={req.job_id})")
            return error_response("cancelled", 499, details=result)
        status_code = 429 if result.get("error") == "rate_limited" else 500
        return error_response(result.get("error", "Error desconocido"), status_code, details=result)
    except JobCancelled as e:
        # Propagada desde los loops internos via check_and_raise().
        log_info(f"[cancel] JobCancelled propagada (job={req.job_id}): {e}")
        return error_response("cancelled", 499, details={"job_id": req.job_id, "cancelled": True})
    except Exception as e:
        return error_response(str(e), 500)
    finally:
        # Limpiar la bandera siempre (exito, error, cancel) para que el
        # set del registry no crezca indefinidamente.
        if req.job_id:
            clear_cancel_flag(req.job_id)

@app.post("/veo3/extend-video")
def veo3_extend_video(req: Veo3ExtendVideoRequest):
    from profile_pool import acquire_port_lock, acquire_pool_semaphore
    try:
        port, err = resolve_pool_port(req.port, "video")
        if err:
            return error_response(err, 503)
        with acquire_pool_semaphore("video"), acquire_port_lock(port):
            from chat_veo3_videos.exten_video import extend_video
            result = extend_video(port=port, prompt=req.prompt)
        if result.get("success"):
            return success_response(data=result, message="Prompt de extension enviado")
        return error_response(result.get("error", "Error desconocido"), 500, details=result)
    except Exception as e:
        return error_response(str(e), 500)

def _notify_webhook(url: str, job_id: str, result: dict) -> None:
    """Fire-and-forget: notifica al webhook sin bloquear el response."""
    if not url:
        return

    def _send():
        try:
            payload = _json.dumps({
                "job_id": job_id,
                "event": "image_ready",
                "file_path": result.get("file_path", ""),
                "file_name": result.get("file_name", ""),
                "file_size": result.get("file_size", 0),
                "image_url": result.get("image_url", ""),
            }, ensure_ascii=False).encode("utf-8")
            req = urllib.request.Request(
                url, data=payload,
                headers={"Content-Type": "application/json; charset=utf-8"},
            )
            urllib.request.urlopen(req, timeout=10)
        except Exception as e:
            print(f"[WARN] Webhook fallo ({url}): {e}")

    threading.Thread(target=_send, daemon=True).start()


def _notify_video_webhook(url: str, job_id: str, result: dict) -> None:
    """Fire-and-forget: notifica al webhook de video listo (mismo patron que _notify_webhook)."""
    if not url:
        return

    def _send():
        try:
            payload = _json.dumps({
                "job_id": job_id,
                "event": "video_ready",
                "file_path": result.get("file_path", ""),
                "file_name": result.get("file_name", ""),
                "file_size": result.get("file_size", 0),
                "video_url": result.get("video_url", ""),
                "duration": result.get("duration", 0),
            }, ensure_ascii=False).encode("utf-8")
            req = urllib.request.Request(
                url, data=payload,
                headers={"Content-Type": "application/json; charset=utf-8"},
            )
            urllib.request.urlopen(req, timeout=10)
        except Exception as e:
            print(f"[WARN] Video webhook fallo ({url}): {e}")

    threading.Thread(target=_send, daemon=True).start()


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="DICloak Control API Server")
    parser.add_argument("--port", type=int, default=SERVER_PORT)
    parser.add_argument("--dicloak-port", type=int, default=DICLOAK_PORT)
    args = parser.parse_args()

    server_port = args.port
    dicloak_port = args.dicloak_port

    print(f"=== DICloak Control API v1.0 ===")
    print(f"Server:  http://127.0.0.1:{server_port}")
    print(f"DICloak: CDP puerto {dicloak_port}")
    print(f"================================")

    ensure_dicloak_running(dicloak_port)

    # Limpiar estado de sesiones anteriores
    write_cdp_debug_info({})
    print("[OK] cdp_debug_info limpiado")

    # Esperar a que DiCloak cargue su página
    for _ in range(15):
        if _get_page_ws_url(dicloak_port):
            break
        time.sleep(1)

    # Conectar CDP + hook
    if init_cdp(dicloak_port):
        print("[OK] CDP conectado y hook inyectado — listo para abrir perfiles")
    else:
        print("[WARN] No se pudo conectar CDP al iniciar — se reintentará en cada request")

    # Pre-cachear estado de Open API (evita ~2s en primera request)
    try:
        if DICloakAPI is not None:
            api = DICloakAPI()
            service._openapi_available = api.is_available()
            if service._openapi_available:
                print("[OK] Open API disponible en puerto", api.port)
            else:
                print("[INFO] Open API no disponible — usando CDP directo")
    except Exception:
        service._openapi_available = False

    # Mostrar perfiles con CDP activo
    debug_data = read_cdp_debug_info()
    for env_id, entry in debug_data.items():
        if isinstance(entry, dict):
            port = entry.get("debugPort", 0)
            if port and _test_cdp_port(port):
                print(f"[OK] Navegador activo — CDP puerto {port} | http://127.0.0.1:{port}/json/version")

    dev_mode = os.environ.get("DEV_RELOAD", "0") == "1"
    # timeout_graceful_shutdown=8: si tras 8s los threads no terminaron
    # (porque estan en CDP/_ws.recv bloqueante), uvicorn fuerza el exit.
    # Combinado con request_shutdown() en el lifespan + interruptible_sleep
    # en los loops, el caso normal cierra en <1s y el peor en ~8s.
    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=server_port,
        log_level="info",
        reload=dev_mode,
        reload_dirs=[str(PROJECT_ROOT)] if dev_mode else [],
        timeout_graceful_shutdown=8,
    )


if __name__ == "__main__":
    main()
