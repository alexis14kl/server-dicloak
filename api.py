"""
DICloak REST API Client.

Endpoints documentados de DICloak Open API (puerto default 52140):
  - GET  /api/v1/env/list          → Lista todos los perfiles
  - POST /api/v1/env/open          → Abre un perfil (devuelve debug port)
  - POST /api/v1/env/close         → Cierra un perfil
  - GET  /api/v1/env/running       → Lista perfiles abiertos + puertos

Autenticación via header X-API-KEY o query param apiSecret.
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from typing import Optional

from logger import log_info, log_ok, log_warn, log_error


@dataclass
class DICloakProfile:
    """Perfil de DICloak."""
    id: str
    name: str
    status: str = ""         # "running", "stopped", etc.
    debug_port: int = 0
    ws_url: str = ""
    pid: int = 0


@dataclass
class DICloakAPI:
    """Cliente REST para DICloak Open API."""

    port: int = 0
    api_key: str = ""
    host: str = "127.0.0.1"
    timeout: int = 2

    def __post_init__(self):
        if not self.port:
            self.port = int(os.environ.get("DICLOAK_API_PORT", "0") or "0") or 52140
        if not self.api_key:
            self.api_key = (os.environ.get("DICLOAK_API_KEY", "") or "").strip()

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    # ── HTTP helpers ──────────────────────────────────────────────────────

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            h["X-API-KEY"] = self.api_key
            h["x-openapi-key"] = self.api_key
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        """Ejecuta un request HTTP a la API de DICloak."""
        url = f"{self.base_url}{path}"
        if self.api_key and "?" not in path:
            url += f"?apiSecret={self.api_key}"
        elif self.api_key:
            url += f"&apiSecret={self.api_key}"

        data = json.dumps(body).encode("utf-8") if body else None
        req = urllib.request.Request(url, data=data, headers=self._headers(), method=method)

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw.strip() else {}
        except urllib.error.HTTPError as e:
            body_text = e.read().decode("utf-8", errors="ignore")[:300]
            raise ConnectionError(f"DICloak API {method} {path}: HTTP {e.code} — {body_text}")
        except urllib.error.URLError as e:
            raise ConnectionError(f"DICloak API no responde en {self.base_url}: {e.reason}")

    def _get(self, path: str) -> dict:
        return self._request("GET", path)

    def _post(self, path: str, body: dict | None = None) -> dict:
        return self._request("POST", path, body)

    # ── API Methods ───────────────────────────────────────────────────────

    def is_available(self) -> bool:
        """Verifica si la API de DICloak responde."""
        try:
            self._get("/api/v1/env/list")
            return True
        except Exception:
            return False

    def list_profiles(self) -> list[DICloakProfile]:
        """Lista todos los perfiles configurados en DICloak."""
        # Intentar múltiples endpoints (varían entre versiones)
        endpoints = [
            "/api/v1/env/list",
            "/v1/env/list",
            "/v1/openapi/env/list",
            "/api/v1/env/list?page=1&pageSize=100",
        ]
        for ep in endpoints:
            try:
                data = self._get(ep)
                profiles = self._extract_profiles(data)
                if profiles:
                    return profiles
            except Exception:
                continue
        return []

    def list_running(self) -> list[DICloakProfile]:
        """Lista perfiles actualmente abiertos con sus puertos CDP."""
        endpoints = [
            "/api/v1/env/running",
            "/v1/env/running",
            "/v1/env/running/list",
            "/v1/openapi/env/running/list",
            "/v1/openapi/running_env/list",
        ]
        for ep in endpoints:
            try:
                data = self._get(ep)
                profiles = self._extract_profiles(data)
                if profiles:
                    return profiles
            except Exception:
                continue
        return []

    def open_profile(self, profile_id: str, wait_for_port: bool = True, timeout_sec: int = 30) -> DICloakProfile:
        """
        Abre un perfil de DICloak.
        Si wait_for_port=True, espera hasta que el debug port esté disponible.
        """
        endpoints = [
            "/api/v1/env/open",
            "/v1/env/open",
            "/v1/openapi/env/open",
        ]

        result = None
        for ep in endpoints:
            try:
                result = self._post(ep, {"id": profile_id})
                break
            except Exception:
                try:
                    result = self._post(ep, {"envId": profile_id})
                    break
                except Exception:
                    continue

        if result is None:
            raise ConnectionError(f"No se pudo abrir el perfil {profile_id} — ningún endpoint respondió.")

        # Extraer debug port de la respuesta
        port = self._extract_port(result)
        ws_url = self._extract_ws_url(result)

        if port and self._test_cdp(port):
            log_ok(f"Perfil abierto via API. CDP en puerto {port}")
            return DICloakProfile(id=profile_id, name="", status="running", debug_port=port, ws_url=ws_url)

        # Si no viene puerto en la respuesta, polling
        if wait_for_port:
            log_info(f"Esperando puerto CDP para perfil {profile_id}...")
            port = self._wait_for_port(profile_id, timeout_sec)
            if port:
                return DICloakProfile(id=profile_id, name="", status="running", debug_port=port)

        raise RuntimeError(f"Perfil {profile_id} abierto pero no se detectó puerto CDP.")

    def close_profile(self, profile_id: str) -> bool:
        """Cierra un perfil abierto."""
        endpoints = [
            "/api/v1/env/close",
            "/v1/env/close",
            "/v1/openapi/env/close",
        ]
        for ep in endpoints:
            try:
                self._post(ep, {"id": profile_id})
                return True
            except Exception:
                try:
                    self._post(ep, {"envId": profile_id})
                    return True
                except Exception:
                    continue
        return False

    def find_profile_by_name(self, name: str) -> Optional[DICloakProfile]:
        """Busca un perfil por nombre (case-insensitive, partial match)."""
        profiles = self.list_profiles()
        target = name.lower().strip()

        # Exact match
        for p in profiles:
            if p.name.lower().strip() == target:
                return p

        # Partial match
        for p in profiles:
            if target in p.name.lower() or p.name.lower() in target:
                return p

        return None

    def open_profile_by_name(self, name: str, timeout_sec: int = 30) -> DICloakProfile:
        """Busca un perfil por nombre y lo abre."""
        # Primero verificar si ya está running
        running = self.list_running()
        for p in running:
            if name.lower() in p.name.lower() or p.name.lower() in name.lower():
                if p.debug_port and self._test_cdp(p.debug_port):
                    log_ok(f"Perfil '{p.name}' ya está abierto. CDP en puerto {p.debug_port}")
                    return p

        # Buscar y abrir
        profile = self.find_profile_by_name(name)
        if not profile:
            raise ValueError(f"Perfil '{name}' no encontrado en DICloak. Perfiles disponibles: {[p.name for p in self.list_profiles()]}")

        log_info(f"Abriendo perfil '{profile.name}' (ID: {profile.id})...")
        return self.open_profile(profile.id, wait_for_port=True, timeout_sec=timeout_sec)

    # ── Internal helpers ──────────────────────────────────────────────────

    def _extract_profiles(self, data: dict) -> list[DICloakProfile]:
        """Extrae perfiles de cualquier estructura de respuesta."""
        profiles = []

        # Buscar lista en diferentes estructuras de respuesta
        items = []
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            for key in ["data", "list", "envs", "profiles", "items", "result"]:
                val = data.get(key)
                if isinstance(val, list):
                    items = val
                    break
                elif isinstance(val, dict) and "list" in val:
                    items = val["list"]
                    break

        for item in items:
            if not isinstance(item, dict):
                continue
            profile = DICloakProfile(
                id=str(item.get("id") or item.get("envId") or item.get("profileId") or ""),
                name=str(item.get("name") or item.get("envName") or item.get("profileName") or ""),
                status=str(item.get("status") or item.get("state") or ""),
                debug_port=self._extract_port(item),
                ws_url=self._extract_ws_url(item),
                pid=int(item.get("pid") or item.get("processPid") or 0),
            )
            if profile.id:
                profiles.append(profile)

        return profiles

    def _extract_port(self, data: dict) -> int:
        """Extrae el debug port de cualquier estructura."""
        for key in ["debugPort", "debug_port", "port", "cdpPort", "remoteDebuggingPort", "debuggingPort"]:
            val = data.get(key)
            if val and int(val) > 0:
                return int(val)
        # Buscar en nested objects
        for val in data.values():
            if isinstance(val, dict):
                port = self._extract_port(val)
                if port:
                    return port
        return 0

    def _extract_ws_url(self, data: dict) -> str:
        """Extrae WebSocket URL."""
        for key in ["webSocketDebuggerUrl", "wsUrl", "ws_url", "webSocketUrl"]:
            val = data.get(key)
            if val and str(val).startswith("ws"):
                return str(val)
        return ""

    def _test_cdp(self, port: int) -> bool:
        """Verifica si un puerto CDP responde."""
        try:
            url = f"http://127.0.0.1:{port}/json/version"
            with urllib.request.urlopen(url, timeout=3) as resp:
                return "webSocketDebuggerUrl" in resp.read().decode("utf-8", errors="ignore")
        except Exception:
            return False

    def _wait_for_port(self, profile_id: str, timeout_sec: int = 30) -> int:
        """Polling: espera hasta que el perfil tenga un puerto CDP activo."""
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            running = self.list_running()
            for p in running:
                if p.id == profile_id and p.debug_port and self._test_cdp(p.debug_port):
                    log_ok(f"CDP detectado en puerto {p.debug_port}")
                    return p.debug_port
            time.sleep(1)
        return 0
