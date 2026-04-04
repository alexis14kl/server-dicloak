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

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
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
from api import DICloakAPI
from logger import log_info, log_ok, log_warn

# ── Config ────────────────────────────────────────────────────────────────────

SERVER_PORT = int(os.environ.get("DICLOAK_API_PORT", "0") or "0") or 8585
DICLOAK_PORT = int(os.environ.get("CDP_DICLOAK_PORT", "0") or "0") or DEFAULT_DICLOAK_PORT
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


# ── Service layer ─────────────────────────────────────────────────────────────

class DICloakService:

    def __init__(self, dicloak_port: int = DEFAULT_DICLOAK_PORT):
        self.port = dicloak_port

    def check_health(self) -> dict:
        ready = is_dicloak_ready(self.port)
        targets = get_dicloak_targets(self.port) if ready else []
        return {
            "dicloak_cdp_port": self.port,
            "dicloak_ready": ready,
            "targets_count": len(targets),
        }

    def get_profiles(self) -> list[dict]:
        # Intentar primero via REST API (puerto 52140) — más fiable que scraping DOM
        try:
            api = DICloakAPI()
            profiles = api.list_profiles()
            if profiles:
                return [{"id": p.id, "name": p.name, "status": p.status} for p in profiles]
        except Exception:
            pass

        # Fallback: scraping del DOM via CDP
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

    def _detect_cdp_port_fast(self, known_ports: set[int], timeout: int = 30) -> int:
        """Detecta un puerto CDP nuevo combinando cdp_debug_info.json y escaneo de procesos.

        Alterna entre polling rápido del archivo JSON y escaneo de procesos
        cada ~10 segundos para maximizar la probabilidad de detección.
        """
        deadline = time.time() + timeout
        process_scan_interval = 8  # escanear procesos cada N segundos
        last_scan = 0

        while time.time() < deadline:
            elapsed = time.time() - (deadline - timeout)

            # cdp_debug_info.json — check rápido (< 1ms)
            data = read_cdp_debug_info()
            for entry in data.values():
                if not isinstance(entry, dict):
                    continue
                try:
                    port = int(entry.get("debugPort") or entry.get("port") or 0)
                except (TypeError, ValueError):
                    continue
                if port and port not in known_ports and _test_cdp_port(port):
                    log_ok(f"Puerto CDP detectado via cdp_debug_info: {port}")
                    return port

            # Escaneo de procesos — cada ~8s (tarda ~1s en PowerShell)
            if elapsed - last_scan >= process_scan_interval:
                last_scan = elapsed
                for p in self.get_running_profiles():
                    port = p.get("debug_port", 0)
                    if port and port not in known_ports and p.get("cdp_active"):
                        log_ok(f"Puerto CDP detectado via proceso: {port}")
                        return port

            time.sleep(0.5)

        log_warn("No se detectó puerto CDP en el tiempo límite")
        return 0

    def open_profile(self, name: str, timeout: int = 60) -> dict:
        if not name:
            raise ValueError("El nombre del perfil es requerido.")
        if not is_dicloak_ready(self.port):
            raise ConnectionError("DICloak no responde. Verifica que este abierto.")

        # ── Capturar puertos CDP ya existentes (rápido: solo cdp_debug_info) ─
        known_ports = {self.port}
        data = read_cdp_debug_info()
        for entry in data.values():
            if isinstance(entry, dict):
                try:
                    p = int(entry.get("debugPort") or entry.get("port") or 0)
                    if p:
                        known_ports.add(p)
                except (TypeError, ValueError):
                    pass

        # ── Preparar: limpiar + inyectar hook ────────────────────────────
        write_cdp_debug_info({})
        inject_cdp_hook(self.port)

        # ── Abrir perfil via click en la UI ──────────────────────────────
        clicked = open_profile_via_cdp(name, self.port)
        if not clicked:
            available = [p["name"] for p in self.get_profiles()]
            raise FileNotFoundError(
                f"Perfil '{name}' no encontrado. Disponibles: {available}"
            )

        # ── Detectar el nuevo puerto CDP (max ~30s) ──────────────────────
        port = self._detect_cdp_port_fast(known_ports, timeout=min(timeout, 40))
        return {
            "name": name,
            "debug_port": port,
            "ws_url": "",
            "cdp_active": port > 0,
        }

    def close_profiles(self) -> int:
        try:
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/F", "/IM", "ginsbrowser.exe"],
                               capture_output=True, timeout=5)
            else:
                subprocess.run(["pkill", "-f", "ginsbrowser"],
                               capture_output=True, timeout=5)
            write_cdp_debug_info({})

            # Limpiar estado zombie en DICloak: navegar fuera y volver
            # para que la UI sincronice el estado real de los procesos
            try:
                cdp_evaluate_sync("location.hash = '#/personalInfo'", self.port, timeout=3)
                time.sleep(2)
                _ensure_on_profile_list(self.port)
                time.sleep(2)
                inject_cdp_hook(self.port)
            except Exception:
                pass

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

class PromptRequest(BaseModel):
    port: int
    prompt: str
    wait_response: bool = False
    timeout: int = 120
    auto_rotate: bool = True

    @field_validator("prompt")
    @classmethod
    def prompt_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("El prompt no puede estar vacío")
        return v.strip()

class ImageDownloadRequest(BaseModel):
    port: int
    output_dir: str = ""
    timeout: int = 300
    webhook_url: str = ""
    job_id: str = ""

class Veo3StabilizeRequest(BaseModel):
    port: int
    timeout: int = 60

class Veo3ExtendVideoRequest(BaseModel):
    port: int
    prompt: str

class Veo3DownloadVideoRequest(BaseModel):
    port: int
    timeout: int = 600
    output_dir: str = ""


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

app = FastAPI(title="DICloak Control API", version="1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# Servir imágenes descargadas via /files/images/
app.mount("/files/images", StaticFiles(directory=str(IMAGES_DIR)), name="images")

service = DICloakService(DICLOAK_PORT)


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
            "POST /chatgpt/prompt",
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

@app.get("/profiles/search/{name}")
def search_profile(name: str):
    try:
        profiles = service.get_profiles()
        target = name.lower().strip()
        matches = [p for p in profiles if target in p["name"].lower() or p["name"].lower() in target]
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


@app.post("/chatgpt/prompt")
def chatgpt_prompt(req: PromptRequest):
    try:
        if req.auto_rotate:
            from chat_gpt_consulta.prompt_paste import paste_and_send_with_rotation
            result = paste_and_send_with_rotation(
                port=req.port,
                prompt=req.prompt,
                wait_response=req.wait_response,
                timeout=req.timeout,
            )
        else:
            from chat_gpt_consulta.prompt_paste import paste_and_send_prompt
            result = paste_and_send_prompt(
                port=req.port,
                prompt=req.prompt,
                wait_response=req.wait_response,
                timeout=req.timeout,
            )
        if result.get("success"):
            return success_response(data=result, message="Prompt enviado a ChatGPT")
        return error_response(result.get("error", "Error desconocido"), 500, details=result)
    except Exception as e:
        return error_response(str(e), 500)


@app.post("/veo3/stabilize")
def veo3_stabilize(req: Veo3StabilizeRequest):
    try:
        from chat_veo3_videos.veo3_session import navigate_and_stabilize
        result = navigate_and_stabilize(port=req.port, timeout=req.timeout)
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
    try:
        from chat_veo3_videos.veo3_session import open_new_project
        result = open_new_project(port=req.port, prompt=req.prompt)
        if result.get("success"):
            return success_response(data=result, message="Nuevo proyecto abierto en Flow")
        return error_response(result.get("error", "Error desconocido"), 500)
    except Exception as e:
        return error_response(str(e), 500)

@app.post("/veo3/download-video")
def veo3_download_video(req: Veo3DownloadVideoRequest):
    try:
        from chat_veo3_videos.exten_video import download_extended_video
        result = download_extended_video(
            port=req.port,
            timeout=req.timeout,
            output_dir=req.output_dir,
        )
        if result.get("success"):
            return success_response(data=result, message="Video descargado")
        return error_response(result.get("error", "Error desconocido"), 500, details=result)
    except Exception as e:
        return error_response(str(e), 500)

@app.post("/chatgpt/download-image")
def chatgpt_download_image(req: ImageDownloadRequest):
    try:
        from chat_gpt_consulta.image_download import wait_and_download_image
        # Siempre guardar en el directorio propio del servidor
        result = wait_and_download_image(
            port=req.port,
            output_dir=str(IMAGES_DIR),
            timeout=req.timeout,
        )
        if result.get("success"):
            # Agregar URL HTTP servible por este servidor
            file_name = result.get("file_name", "")
            if file_name:
                result["image_url"] = f"http://127.0.0.1:{SERVER_PORT}/files/images/{file_name}"
            _notify_webhook(req.webhook_url, req.job_id, result)
            return success_response(data=result, message="Imagen descargada")
        return error_response(result.get("error", "Error desconocido"), 500, details=result)
    except Exception as e:
        return error_response(str(e), 500)

@app.post("/veo3/extend-video")
def veo3_extend_video(req: Veo3ExtendVideoRequest):
    try:
        from chat_veo3_videos.exten_video import extend_video
        result = extend_video(port=req.port, prompt=req.prompt)
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

    # Mostrar perfiles con CDP activo
    debug_data = read_cdp_debug_info()
    for env_id, entry in debug_data.items():
        if isinstance(entry, dict):
            port = entry.get("debugPort", 0)
            if port and _test_cdp_port(port):
                print(f"[OK] Navegador activo — CDP puerto {port} | http://127.0.0.1:{port}/json/version")

    dev_mode = os.environ.get("DEV_RELOAD", "0") == "1"
    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=server_port,
        log_level="info",
        reload=dev_mode,
        reload_dirs=[str(PROJECT_ROOT)] if dev_mode else [],
    )


if __name__ == "__main__":
    main()
