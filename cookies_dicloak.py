"""
cookies_dicloak.py — Extrae, guarda y restaura cookies de perfiles DICloak via CDP.

Análogo al manejo de captchaResueltaAt / ViewState en el RPA de Policía:
  - Tras abrir un perfil → extraer y guardar cookies + localStorage
  - Al consultar salud → detectar sesión próxima a expirar (como rewarm a 95s)
  - Al restaurar → inyectar cookies en ginsbrowser frío (como warm-up al arrancar)

Uso standalone:
    python cookies_dicloak.py extract --port 52101 --profile "#1 Chat Gpt Pro"
    python cookies_dicloak.py info    --profile "#1 Chat Gpt Pro"
    python cookies_dicloak.py restore --port 52101 --profile "#1 Chat Gpt Pro"

Como módulo desde server.py / FastAPI:
    from cookies_dicloak import CookieManager
    mgr = CookieManager()
    mgr.extract_and_save(port=52101, page_id="...", profile_name="#1 Chat Gpt Pro")
    info = mgr.session_info("#1 Chat Gpt Pro")
"""
from __future__ import annotations

import json
import time
import urllib.request
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

# ── Dominios que valen la pena guardar ────────────────────────────────────────

RELEVANT_DOMAINS = {
    ".chatgpt.com", "chatgpt.com",
    ".openai.com",  "openai.com",
    ".auth.openai.com", "auth.openai.com",
    ".sentinel.openai.com",
    ".ws.chatgpt.com", "ws.chatgpt.com",
    "accounts.google.com", ".google.com",
}

# Cookies críticas para la sesión (las más importantes)
SESSION_COOKIE_NAMES = {
    "__Secure-next-auth.session-token.0",
    "__Secure-next-auth.session-token.1",
    "__Secure-next-auth.session-token",
    "cf_clearance",
    "usc_S3n5iUHonfCcG10PzwnIyO2S",  # puede variar por cuenta
    "unified_session_manifest",
    "__Secure-oai-is",
    "oai-sc",
    "oai-client-auth-info",
    "_puid",
    "oai-did",
    "_account",
}

# ── Estructuras de datos ───────────────────────────────────────────────────────

@dataclass
class SessionInfo:
    profile_name: str
    user_id: str = ""
    account: str = ""
    session_expires_at: float = 0.0      # Unix timestamp
    cf_clearance_expires_at: float = 0.0
    saved_at: float = 0.0
    cookies_count: int = 0
    localStorage_keys: list = field(default_factory=list)

    @property
    def session_expires_in_days(self) -> float:
        if self.session_expires_at <= 0:
            return -1.0
        return (self.session_expires_at - time.time()) / 86400

    @property
    def cf_expires_in_days(self) -> float:
        if self.cf_clearance_expires_at <= 0:
            return -1.0
        return (self.cf_clearance_expires_at - time.time()) / 86400

    @property
    def is_session_valid(self) -> bool:
        return self.session_expires_in_days > 0

    @property
    def needs_rewarm(self) -> bool:
        """True si la sesión expira en menos de 7 días (análogo a rewarm a 95s en policia)."""
        d = self.session_expires_in_days
        return 0 < d < 7

    def to_dict(self) -> dict:
        return {
            "profile_name": self.profile_name,
            "user_id": self.user_id,
            "account": self.account,
            "session_expires_at": self.session_expires_at,
            "session_expires_in_days": round(self.session_expires_in_days, 1),
            "cf_clearance_expires_in_days": round(self.cf_expires_in_days, 1),
            "is_session_valid": self.is_session_valid,
            "needs_rewarm": self.needs_rewarm,
            "saved_at": self.saved_at,
            "cookies_count": self.cookies_count,
            "localStorage_keys": self.localStorage_keys,
        }


# ── Manager principal ──────────────────────────────────────────────────────────

class CookieManager:
    def __init__(self, cache_dir: str | Path | None = None):
        if cache_dir is None:
            cache_dir = Path(__file__).resolve().parent / "cache" / "cookies"
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _profile_path(self, profile_name: str) -> Path:
        safe = profile_name.replace("/", "_").replace("\\", "_").replace(" ", "_")
        return self.cache_dir / f"{safe}.json"

    # ── CDP helpers ───────────────────────────────────────────────────────────

    def _get_page_id(self, port: int, prefer_url: str = "chatgpt.com") -> str:
        """Devuelve el target ID de la pestaña principal (chatgpt.com o la primera page)."""
        try:
            targets = json.loads(
                urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=3).read()
            )
            for t in targets:
                if t.get("type") == "page" and prefer_url in t.get("url", ""):
                    return t["id"]
            for t in targets:
                if t.get("type") == "page":
                    return t["id"]
        except Exception:
            pass
        return ""

    def _cdp_send(self, ws, msg_id: int, method: str, params: dict | None = None) -> None:
        import websockets.sync.client as _wsc
        payload: dict = {"id": msg_id, "method": method}
        if params:
            payload["params"] = params
        ws.send(json.dumps(payload))

    def _cdp_connect(self, port: int, page_id: str):
        import websockets.sync.client as wsc
        return wsc.connect(
            f"ws://127.0.0.1:{port}/devtools/page/{page_id}",
            max_size=2 ** 24,
        )

    # ── Extracción ────────────────────────────────────────────────────────────

    def extract(self, port: int, page_id: str = "") -> dict:
        """
        Extrae cookies, localStorage y sessionStorage del ginsbrowser via CDP.
        Devuelve dict con todas las claves, listo para guardar.
        """
        if not page_id:
            page_id = self._get_page_id(port)
        if not page_id:
            raise RuntimeError(f"No se encontró page target en puerto {port}")

        ws = self._cdp_connect(port, page_id)
        try:
            self._cdp_send(ws, 1, "Network.getAllCookies")
            self._cdp_send(ws, 2, "Runtime.evaluate", {
                "expression": "JSON.stringify(Object.fromEntries(Object.entries(localStorage)))",
                "returnByValue": True,
            })
            self._cdp_send(ws, 3, "Runtime.evaluate", {
                "expression": "JSON.stringify(Object.fromEntries(Object.entries(sessionStorage)))",
                "returnByValue": True,
            })

            results: dict[int, Any] = {}
            deadline = time.time() + 8
            while len(results) < 3 and time.time() < deadline:
                try:
                    ws.socket.settimeout(1)
                    msg = json.loads(ws.recv())
                    mid = msg.get("id")
                    if mid in (1, 2, 3):
                        results[mid] = msg.get("result", {})
                except Exception:
                    pass
        finally:
            ws.close()

        # Guardar TODAS las cookies — sin filtrar por dominio
        # El filtro anterior solo tenía ChatGPT/OpenAI y rompía Meta AI, Leonardo, etc.
        all_cookies: list[dict] = results.get(1, {}).get("cookies", [])

        ls_raw = results.get(2, {}).get("result", {}).get("value", "") or "{}"
        ss_raw = results.get(3, {}).get("result", {}).get("value", "") or "{}"

        local_storage = json.loads(ls_raw)
        session_storage = json.loads(ss_raw)

        return {
            "cookies": all_cookies,
            "localStorage": local_storage,
            "sessionStorage": session_storage,
            "extracted_at": time.time(),
        }

    def extract_and_save(
        self,
        port: int,
        profile_name: str,
        page_id: str = "",
    ) -> SessionInfo:
        """
        Extrae cookies del ginsbrowser y las guarda en cache/cookies/{profile}.json.
        Devuelve SessionInfo con el estado de la sesión.
        """
        data = self.extract(port, page_id)
        data["profile_name"] = profile_name

        path = self._profile_path(profile_name)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

        return self._build_info(profile_name, data)

    # ── Restauración ──────────────────────────────────────────────────────────

    def restore(self, port: int, profile_name: str, page_id: str = "") -> dict:
        """
        Inyecta las cookies guardadas en el ginsbrowser via CDP.
        Útil para warm-up: restaurar sesión antes de que la página autentique.
        Devuelve {"restored": N, "failed": M, "skipped_expired": K}
        """
        path = self._profile_path(profile_name)
        if not path.exists():
            raise FileNotFoundError(f"No hay cache guardado para '{profile_name}'")

        saved = json.loads(path.read_text(encoding="utf-8"))
        cookies: list[dict] = saved.get("cookies", [])
        local_storage: dict = saved.get("localStorage", {})

        if not page_id:
            page_id = self._get_page_id(port)
        if not page_id:
            raise RuntimeError(f"No se encontró page target en puerto {port}")

        ws = self._cdp_connect(port, page_id)
        now = time.time()
        restored = failed = skipped = 0

        try:
            for i, cookie in enumerate(cookies):
                # Saltar cookies expiradas (excepto las de sesión que tienen -1)
                expires = cookie.get("expires", -1)
                if expires != -1 and expires < now:
                    skipped += 1
                    continue

                # Construir parámetros para Network.setCookie
                params: dict = {
                    "name":     cookie["name"],
                    "value":    cookie["value"],
                    "domain":   cookie.get("domain", ""),
                    "path":     cookie.get("path", "/"),
                    "secure":   cookie.get("secure", False),
                    "httpOnly": cookie.get("httpOnly", False),
                    "sameSite": cookie.get("sameSite", "Lax"),
                }
                if expires != -1:
                    params["expires"] = expires

                self._cdp_send(ws, 100 + i, "Network.setCookie", params)

            # Leer respuestas de setCookie
            deadline = time.time() + 5
            while time.time() < deadline:
                try:
                    ws.socket.settimeout(0.3)
                    msg = json.loads(ws.recv())
                    if msg.get("id", 0) >= 100:
                        if msg.get("result", {}).get("success"):
                            restored += 1
                        else:
                            failed += 1
                except Exception:
                    break

            # Restaurar localStorage en chunks de 10 para evitar crash con payloads grandes
            if local_storage:
                items_ls = list(local_storage.items())
                chunk_size_ls = 10
                msg_id_ls = 900
                for i_ls in range(0, len(items_ls), chunk_size_ls):
                    chunk_ls = items_ls[i_ls:i_ls+chunk_size_ls]
                    ls_js = ";".join(
                        f"localStorage.setItem({json.dumps(k)}, {json.dumps(str(v))})"
                        for k, v in chunk_ls
                    )
                    self._cdp_send(ws, msg_id_ls, "Runtime.evaluate", {"expression": ls_js})
                    msg_id_ls += 1
                    time.sleep(0.2)

        finally:
            ws.close()

        return {
            "profile_name": profile_name,
            "restored_cookies": restored,
            "failed_cookies": failed,
            "skipped_expired": skipped,
            "localStorage_keys_restored": len(local_storage),
        }

    # ── Información de sesión ─────────────────────────────────────────────────

    def session_info(self, profile_name: str) -> SessionInfo:
        """Lee el cache guardado y devuelve el estado de la sesión (sin abrir el navegador)."""
        path = self._profile_path(profile_name)
        if not path.exists():
            return SessionInfo(profile_name=profile_name)
        data = json.loads(path.read_text(encoding="utf-8"))
        return self._build_info(profile_name, data)

    def _build_info(self, profile_name: str, data: dict) -> SessionInfo:
        cookies: list[dict] = data.get("cookies", [])
        local_storage: dict = data.get("localStorage", {})

        # Buscar token de sesión y su expiración
        session_expires = 0.0
        cf_expires = 0.0
        user_id = ""
        account = ""

        for c in cookies:
            name = c.get("name", "")
            expires = float(c.get("expires", -1))

            if name == "__Secure-next-auth.session-token.0":
                session_expires = expires
            if name == "cf_clearance" and c.get("domain", "").endswith("chatgpt.com"):
                cf_expires = expires

        # user_id desde localStorage
        for key in local_storage:
            if key.startswith("oai/apps/chatTheme/user-"):
                user_id = key.split("user-")[-1].split("/")[0]
                break
        if not user_id:
            for key in local_storage:
                if "user-" in key:
                    user_id = key.split("user-")[-1].split("/")[0][:28]
                    break

        account = local_storage.get("_account", "")

        return SessionInfo(
            profile_name=profile_name,
            user_id=user_id,
            account=account,
            session_expires_at=session_expires,
            cf_clearance_expires_at=cf_expires,
            saved_at=data.get("extracted_at", 0.0),
            cookies_count=len(cookies),
            localStorage_keys=list(local_storage.keys())[:15],
        )

    def list_profiles(self) -> list[dict]:
        """Lista todos los perfiles con cache guardado y su estado de sesión."""
        result = []
        for path in sorted(self.cache_dir.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                profile_name = data.get("profile_name", path.stem)
                info = self._build_info(profile_name, data)
                result.append(info.to_dict())
            except Exception:
                pass
        return result


# ── Inyección de shortcuts de teclado ─────────────────────────────────────────

def inject_shortcuts(port: int, page_id: str = "") -> bool:
    """
    Inyecta listener de teclado en el ginsbrowser para habilitar F12 / Ctrl+Shift+I.
    Se llama automáticamente desde server.py tras cada open_profile exitoso.
    Retorna True si se inyectó correctamente.

    Análogo al warm-up de Buster en el RPA Policía:
      - Policía: arranca → warmup() → Buster despierto + token cacheado
      - DICloak: open_profile → inject_shortcuts() → F12 funcionando
    """
    try:
        import websockets.sync.client as wsc

        if not page_id:
            page_id = _get_page_id_for_shortcuts(port)
        if not page_id:
            return False

        shortcut_js = f"""
(() => {{
  if (window.__SHORTCUTS_ENABLED__) return 'already';
  window.__SHORTCUTS_ENABLED__ = true;
  const PORT = {port};
  const PID  = '{page_id}';
  document.addEventListener('keydown', e => {{
    const ctrl  = e.ctrlKey || e.metaKey;
    const shift = e.shiftKey;
    if (e.key === 'F12' || (ctrl && shift && e.key === 'I') || (ctrl && shift && e.key === 'J')) {{
      e.stopImmediatePropagation();
      window.open(
        `http://127.0.0.1:${{PORT}}/devtools/inspector.html?ws=127.0.0.1:${{PORT}}/devtools/page/${{PID}}`,
        '_blank'
      );
    }}
  }}, true);
  return 'ok';
}})()
"""
        ws = wsc.connect(
            f"ws://127.0.0.1:{port}/devtools/page/{page_id}",
            max_size=2 ** 23,
        )
        try:
            ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                                "params": {"expression": shortcut_js, "returnByValue": True}}))
            ws.send(json.dumps({"id": 2, "method": "Page.addScriptToEvaluateOnNewDocument",
                                "params": {"source": shortcut_js}}))
            deadline = time.time() + 4
            while time.time() < deadline:
                try:
                    ws.socket.settimeout(0.5)
                    msg = json.loads(ws.recv())
                    if msg.get("id") == 2:
                        break
                except Exception:
                    pass
        finally:
            ws.close()
        return True
    except Exception:
        return False


def _get_page_id_for_shortcuts(port: int, prefer: str = "chatgpt.com") -> str:
    try:
        targets = json.loads(
            urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=3).read()
        )
        for t in targets:
            if t.get("type") == "page" and prefer in t.get("url", ""):
                return t["id"]
        for t in targets:
            if t.get("type") == "page":
                return t["id"]
    except Exception:
        pass
    return ""


# ── FastAPI router (importar en server.py) ────────────────────────────────────

def build_router(manager: CookieManager | None = None):
    """
    Devuelve un APIRouter de FastAPI con los endpoints de cookies.
    Uso en server.py:
        from cookies_dicloak import build_router, CookieManager
        cookie_mgr = CookieManager()
        app.include_router(build_router(cookie_mgr), prefix="/cookies")
    """
    from fastapi import APIRouter, Body
    from fastapi.responses import Response
    import json as _json

    router = APIRouter(tags=["cookies"])
    mgr = manager or CookieManager()

    def ok(data=None, msg="OK"):
        body = {"success": True, "message": msg}
        if data is not None:
            body["data"] = data
        return Response(
            content=_json.dumps(body, indent=2, ensure_ascii=False),
            media_type="application/json",
        )

    def err(msg, status=400):
        body = {"success": False, "error": msg}
        return Response(
            content=_json.dumps(body, ensure_ascii=False),
            status_code=status,
            media_type="application/json",
        )

    @router.post("/inject-json")
    def inject_json(port: int, payload: dict = Body(...)):
        """
        Recibe un JSON de cache e inyecta cookies + localStorage en el perfil abierto.
        Body: { "cookies": [...], "localStorage": {...} }
        Ejemplo:
          curl -X POST "http://127.0.0.1:8585/cookies/inject-json?port=53706" \
               -H "Content-Type: application/json" -d @cache.json
        """
        import time as _t, urllib.request as _ur
        import websockets.sync.client as _wsc

        cookies_list  = payload.get("cookies", [])
        local_storage = payload.get("localStorage", {})
        if not cookies_list:
            return err("El JSON no contiene cookies", 400)

        def _get_page(p):
            try:
                targets = _json.loads(_ur.urlopen(f"http://127.0.0.1:{p}/json", timeout=5).read())
                page = next((t for t in targets if t.get("type") == "page"
                             and "chatgpt" in t.get("url", "")), None)
                if not page:
                    page = next((t for t in targets if t.get("type") == "page"), None)
                return page["id"] if page else ""
            except Exception:
                return ""

        page_id = _get_page(port)
        if not page_id:
            return err(f"No hay page target en puerto {port} — perfil abierto?", 404)

        now = _t.time()
        injected = failed = skipped = 0

        # 1. Inyectar cookies
        try:
            ws = _wsc.connect(f"ws://127.0.0.1:{port}/devtools/page/{page_id}", max_size=2**23)
            try:
                for i, cookie in enumerate(cookies_list):
                    expires = cookie.get("expires", -1)
                    if expires != -1 and expires < now:
                        skipped += 1
                        continue
                    params = {
                        "name":     cookie["name"],
                        "value":    cookie["value"],
                        "domain":   cookie.get("domain", ""),
                        "path":     cookie.get("path", "/"),
                        "secure":   cookie.get("secure", False),
                        "httpOnly": cookie.get("httpOnly", False),
                        "sameSite": cookie.get("sameSite", "Lax"),
                    }
                    if expires != -1:
                        params["expires"] = expires
                    ws.send(_json.dumps({"id": 100 + i, "method": "Network.setCookie",
                                         "params": params}))
                deadline = _t.time() + 5
                while _t.time() < deadline:
                    try:
                        ws.socket.settimeout(0.3)
                        msg = _json.loads(ws.recv())
                        if msg.get("id", 0) >= 100:
                            if msg.get("result", {}).get("success"):
                                injected += 1
                            else:
                                failed += 1
                    except Exception:
                        break
            finally:
                try: ws.close()
                except Exception: pass
        except Exception as e:
            return err(f"Error inyectando cookies: {e}", 500)

        # 2. Detectar URL destino desde la página actual (antes de navegar)
        def _nav_from_current_page(p):
            try:
                targets = _json.loads(_ur.urlopen(f"http://127.0.0.1:{p}/json", timeout=5).read())
                for t in targets:
                    url = t.get("url", "")
                    if url and not url.startswith(("about:", "chrome:", "devtools:")):
                        import urllib.parse as _up
                        parsed = _up.urlparse(url)
                        if parsed.netloc:
                            return f"{parsed.scheme}://{parsed.netloc}"
            except Exception:
                pass
            return None

        nav_url = _nav_from_current_page(port) or "https://chatgpt.com"

        # 3. Navegar PRIMERO — localStorage es origin-specific, debe setearse ya en el dominio destino
        try:
            ws_nav = _wsc.connect(
                f"ws://127.0.0.1:{port}/devtools/page/{_get_page(port) or page_id}",
                max_size=2**23)
            ws_nav.send(_json.dumps({"id": 1, "method": "Page.navigate",
                                     "params": {"url": nav_url}}))
            _t.sleep(4)
            ws_nav.close()
        except Exception:
            pass

        # 4. Setear localStorage ya en el dominio correcto (chunks para evitar crash)
        ls_restored = 0
        if local_storage:
            try:
                ws2 = _wsc.connect(
                    f"ws://127.0.0.1:{port}/devtools/page/{_get_page(port) or page_id}",
                    max_size=2**24)
                items = list(local_storage.items())
                chunk_size = 10
                msg_id = 900
                for i in range(0, len(items), chunk_size):
                    chunk = items[i:i+chunk_size]
                    ls_js = ";".join(
                        f"localStorage.setItem({_json.dumps(k)}, {_json.dumps(str(v))})"
                        for k, v in chunk
                    )
                    ws2.send(_json.dumps({"id": msg_id, "method": "Runtime.evaluate",
                                          "params": {"expression": ls_js}}))
                    msg_id += 1
                    _t.sleep(0.2)
                # Reload para que el app lea el localStorage recien seteado
                ws2.send(_json.dumps({"id": 999, "method": "Page.reload", "params": {}}))
                _t.sleep(3)
                ws2.close()
                ls_restored = len(local_storage)
            except Exception:
                pass

        return ok(data={
            "port": port,
            "navigated_to": nav_url,
            "cookies_injected": injected,
            "cookies_failed": failed,
            "cookies_skipped_expired": skipped,
            "localStorage_keys_restored": ls_restored,
        }, msg=f"Sesion inyectada: {injected} cookies, {ls_restored} localStorage keys")

    @router.post("/inject")
    def inject_from_file(payload: dict = Body(...)):
        """
        Inyecta sesion desde un archivo de cache guardado en disco.
        Body: {"port": 55915, "file": "#5_Chat_Gpt_Pro.json"}
        Internamente resuelve cache/cookies/{file} — no hace falta la ruta completa.
        Ejemplo:
          curl -X POST http://127.0.0.1:8585/cookies/inject
               -H "Content-Type: application/json"
               -d "{\"port\": 55915, \"file\": \"#5_Chat_Gpt_Pro.json\"}"
        """
        import pathlib as _pl2, time as _t, urllib.request as _ur
        import websockets.sync.client as _wsc

        port         = payload.get("port")
        file_name    = payload.get("file", "").strip()
        url_override = payload.get("url", "").strip()  # forzar destino

        if not port:
            return err("Falta 'port' en el body", 400)
        if not file_name:
            return err("Falta 'file' en el body (ej: #5_Chat_Gpt_Pro.json)", 400)

        cache_dir  = _pl2.Path(__file__).parent / "cache" / "cookies"
        cache_file = cache_dir / file_name

        if not cache_file.exists():
            available = [f.name for f in cache_dir.glob("*.json")] if cache_dir.exists() else []
            return err(f"Archivo '{file_name}' no encontrado. Disponibles: {available}", 404)

        try:
            cache_data = _json.loads(cache_file.read_text(encoding="utf-8"))
        except Exception as e:
            return err(f"Error leyendo {file_name}: {e}", 500)

        cookies_list  = cache_data.get("cookies", [])
        local_storage = cache_data.get("localStorage", {})

        if not cookies_list:
            return err(f"El archivo '{file_name}' no contiene cookies", 400)

        def _get_page(p):
            try:
                targets = _json.loads(_ur.urlopen(f"http://127.0.0.1:{p}/json", timeout=5).read())
                page = next((t for t in targets if t.get("type") == "page"
                             and "chatgpt" in t.get("url", "")), None)
                if not page:
                    page = next((t for t in targets if t.get("type") == "page"), None)
                return page["id"] if page else ""
            except Exception:
                return ""

        def _get_current_url(p):
            try:
                targets = _json.loads(_ur.urlopen(f"http://127.0.0.1:{p}/json", timeout=5).read())
                page = next((t for t in targets if t.get("type") == "page"), None)
                return page.get("url", "") if page else ""
            except Exception:
                return ""

        page_id = _get_page(port)
        if not page_id:
            return err(f"No hay page target en puerto {port} — perfil abierto?", 404)

        now = _t.time()
        injected = failed = skipped = 0

        try:
            ws = _wsc.connect(f"ws://127.0.0.1:{port}/devtools/page/{page_id}", max_size=2**23)
            try:
                for i, cookie in enumerate(cookies_list):
                    expires = cookie.get("expires", -1)
                    if expires != -1 and expires < now:
                        skipped += 1
                        continue
                    params = {
                        "name":     cookie["name"],
                        "value":    cookie["value"],
                        "domain":   cookie.get("domain", ""),
                        "path":     cookie.get("path", "/"),
                        "secure":   cookie.get("secure", False),
                        "httpOnly": cookie.get("httpOnly", False),
                        "sameSite": cookie.get("sameSite", "Lax"),
                    }
                    if expires != -1:
                        params["expires"] = expires
                    ws.send(_json.dumps({"id": 100 + i, "method": "Network.setCookie",
                                         "params": params}))
                deadline = _t.time() + 5
                while _t.time() < deadline:
                    try:
                        ws.socket.settimeout(0.3)
                        msg = _json.loads(ws.recv())
                        if msg.get("id", 0) >= 100:
                            if msg.get("result", {}).get("success"):
                                injected += 1
                            else:
                                failed += 1
                    except Exception:
                        break
            finally:
                try: ws.close()
                except Exception: pass
        except Exception as e:
            return err(f"Error inyectando cookies: {e}", 500)

        # 2. Detectar URL destino dinamicamente — sin mapa hardcodeado.
        # Toma el dominio mas frecuente entre las cookies, ignorando trackers genericos.
        _GENERIC_DOMAINS = {
            "google.com", "googleapis.com", "gstatic.com", "doubleclick.net",
            "amazon.com", "amazonaws.com", "cloudfront.net", "cloudflare.com",
            "facebook.com", "fbcdn.net", "twitter.com", "youtube.com",
            "microsoft.com", "windows.com", "bing.com",
        }
        if url_override:
            nav_url = url_override
        else:
            # contar cookies por dominio, excluir trackers
            from collections import Counter as _Counter
            domain_counts = _Counter()
            for c in cookies_list:
                d = c.get("domain", "").lstrip(".")
                if d and not any(g in d for g in _GENERIC_DOMAINS):
                    domain_counts[d] += 1
            if domain_counts:
                top_domain = domain_counts.most_common(1)[0][0]
                nav_url = f"https://{top_domain}"
            else:
                nav_url = _get_current_url(port) or "https://chatgpt.com"

        # 3. Detectar si el tab ya esta en el dominio correcto
        import urllib.parse as _up
        current_url = _get_current_url(port)
        current_host = _up.urlparse(current_url).netloc if current_url else ""
        target_host  = _up.urlparse(nav_url).netloc

        already_there = current_host and target_host and (
            current_host == target_host or
            current_host.endswith("." + target_host) or
            target_host.endswith("." + current_host)
        )

        ls_restored = 0
        if already_there:
            # Ya estamos en el dominio correcto — setear localStorage y recargar
            if local_storage:
                try:
                    ws2 = _wsc.connect(
                        f"ws://127.0.0.1:{port}/devtools/page/{_get_page(port) or page_id}",
                        max_size=2**24)
                    items = list(local_storage.items())
                    chunk_size = 10
                    msg_id = 900
                    for i in range(0, len(items), chunk_size):
                        chunk = items[i:i+chunk_size]
                        ls_js = ";".join(
                            f"localStorage.setItem({_json.dumps(k)}, {_json.dumps(str(v))})"
                            for k, v in chunk
                        )
                        ws2.send(_json.dumps({"id": msg_id, "method": "Runtime.evaluate",
                                              "params": {"expression": ls_js}}))
                        msg_id += 1
                        _t.sleep(0.2)
                    ws2.send(_json.dumps({"id": 999, "method": "Page.reload", "params": {}}))
                    _t.sleep(3)
                    ws2.close()
                    ls_restored = len(local_storage)
                except Exception:
                    pass
        else:
            # Dominio diferente — navegar y luego setear localStorage al cargar
            try:
                ws_nav = _wsc.connect(
                    f"ws://127.0.0.1:{port}/devtools/page/{_get_page(port) or page_id}",
                    max_size=2**23)
                # Registrar script que setea localStorage cuando cargue el dominio destino
                if local_storage:
                    ls_entries = ";".join(
                        f"localStorage.setItem({_json.dumps(k)}, {_json.dumps(str(v))})"
                        for k, v in local_storage.items()
                    )
                    init_script = f"""
if (location.hostname === '{target_host}' || location.hostname.endsWith('.{target_host}')) {{
    {ls_entries}
}}
"""
                    ws_nav.send(_json.dumps({"id": 1, "method": "Page.addScriptToEvaluateOnNewDocument",
                                             "params": {"source": init_script}}))
                    _t.sleep(0.3)
                ws_nav.send(_json.dumps({"id": 2, "method": "Page.navigate",
                                         "params": {"url": nav_url}}))
                _t.sleep(4)
                # Verificar si la navegacion funciono
                new_url = _get_current_url(port)
                new_host = _up.urlparse(new_url).netloc if new_url else ""
                nav_ok = new_host and (
                    new_host == target_host or new_host.endswith("." + target_host)
                )
                if nav_ok:
                    ls_restored = len(local_storage)
                ws_nav.close()
            except Exception:
                pass

        return ok(data={
            "port": port,
            "file": file_name,
            "navigated_to": nav_url,
            "cookies_injected": injected,
            "cookies_failed": failed,
            "cookies_skipped_expired": skipped,
            "localStorage_keys_restored": ls_restored,
        }, msg=f"Sesion inyectada desde '{file_name}': {injected} cookies, {ls_restored} localStorage keys")

    @router.post("/extract")
    def smart_extract(payload: dict = Body(default={}), port: int = 0):
        """
        Extrae cookies del perfil abierto.
        Acepta port y name via query param o body JSON.
        Body opcional: {"port": 57257, "name": "#2 Meta AI"}
        Si el nombre no se manda, se detecta automaticamente desde el historial.
        Uso:
          POST /cookies/extract?port=57257
          POST /cookies/extract  body: {"port": 57257, "name": "#2 Meta AI"}
        """
        import pathlib as _pl

        # port y name pueden venir del body o del query param
        port = payload.get("port", port)
        name_override = payload.get("name", "").strip()

        if not port:
            return err("Falta 'port' (query param o body)", 400)

        pm_path = _pl.Path(__file__).parent / "cache" / "port_map.json"

        # Buscar nombre en port_map.json
        profile_name = ""
        try:
            if pm_path.exists():
                pm = _json.loads(pm_path.read_text())
                profile_name = pm.get(str(port), "")
        except Exception:
            pass

        # Si mandaron name explicitamente, usarlo y actualizar el mapa
        if name_override:
            profile_name = name_override
            try:
                pm_path.parent.mkdir(exist_ok=True)
                pm = _json.loads(pm_path.read_text()) if pm_path.exists() else {}
                pm[str(port)] = profile_name
                pm_path.write_text(_json.dumps(pm, indent=2))
            except Exception:
                pass

        if not profile_name:
            available = [f.name for f in (_pl.Path(__file__).parent / "cache" / "cookies").glob("*.json")] \
                        if (_pl.Path(__file__).parent / "cache" / "cookies").exists() else []
            return err(
                f"Puerto {port} no encontrado en historial. "
                f"Enviá el nombre: POST /cookies/extract body: {{\"port\":{port}, \"name\":\"NombrePerfil\"}}. "
                f"Caches disponibles: {available}",
                404,
            )

        try:
            info = mgr.extract_and_save(port=port, profile_name=profile_name)
            d = info.to_dict()
            cnt = d.get("cookies_count", "?")
            return ok(data=d, msg=f"Cache guardado — perfil: {profile_name}, {cnt} cookies")
        except Exception as e:
            return err(str(e), 500)

    @router.get("/")
    def list_cached():
        """Lista todos los perfiles con cache y estado de sesión."""
        return ok(data=mgr.list_profiles())

    @router.get("/{profile_name}/info")
    def get_info(profile_name: str):
        """Estado de sesión de un perfil (sin abrir navegador)."""
        info = mgr.session_info(profile_name)
        return ok(data=info.to_dict())

    @router.post("/{profile_name}/extract")
    def extract(profile_name: str, port: int = 52101, page_id: str = ""):
        """
        Extrae cookies del ginsbrowser abierto y las guarda.
        Llamar justo después de /profiles/open.
        """
        try:
            info = mgr.extract_and_save(port=port, profile_name=profile_name, page_id=page_id)
            return ok(
                data=info.to_dict(),
                msg=f"Cache guardado: {info.cookies_count} cookies, sesión válida por {info.session_expires_in_days:.0f} días",
            )
        except Exception as e:
            return err(str(e), 500)

    @router.post("/{profile_name}/restore")
    def restore(profile_name: str, port: int = 52101, page_id: str = ""):
        """
        Restaura cookies guardadas en el ginsbrowser (warm-up de sesión).
        Llamar antes de que la página haga auth, justo tras abrir el perfil.
        """
        try:
            result = mgr.restore(port=port, profile_name=profile_name, page_id=page_id)
            return ok(
                data=result,
                msg=f"Restauradas {result['restored_cookies']} cookies, "
                    f"{result['skipped_expired']} expiradas omitidas",
            )
        except FileNotFoundError as e:
            return err(str(e), 404)
        except Exception as e:
            return err(str(e), 500)

    return router


# ── CLI standalone ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Gestión de cookies DICloak via CDP")
    sub = parser.add_subparsers(dest="cmd")

    p_extract = sub.add_parser("extract", help="Extraer y guardar cookies del perfil abierto")
    p_extract.add_argument("--port",    type=int, default=52101)
    p_extract.add_argument("--profile", required=True)
    p_extract.add_argument("--page-id", default="")

    p_info = sub.add_parser("info", help="Ver estado de sesión guardada")
    p_info.add_argument("--profile", required=True)

    p_restore = sub.add_parser("restore", help="Restaurar cookies guardadas en navegador abierto")
    p_restore.add_argument("--port",    type=int, default=52101)
    p_restore.add_argument("--profile", required=True)
    p_restore.add_argument("--page-id", default="")

    p_list = sub.add_parser("list", help="Listar todos los perfiles con cache")

    args = parser.parse_args()
    mgr = CookieManager()

    if args.cmd == "extract":
        info = mgr.extract_and_save(args.port, args.profile, args.page_id)
        print(json.dumps(info.to_dict(), indent=2, ensure_ascii=False))

    elif args.cmd == "info":
        info = mgr.session_info(args.profile)
        d = info.to_dict()
        print(f"\nPerfil:          {d['profile_name']}")
        print(f"Usuario ID:      {d['user_id'] or '(desconocido)'}")
        print(f"Cuenta:          {d['account'] or '(desconocida)'}")
        print(f"Sesión válida:   {'✓' if d['is_session_valid'] else '✗'}")
        print(f"Expira en:       {d['session_expires_in_days']} días")
        print(f"CF Clearance:    {d['cf_clearance_expires_in_days']} días")
        print(f"Necesita rewarm: {'⚠ SÍ' if d['needs_rewarm'] else 'No'}")
        print(f"Cookies:         {d['cookies_count']}")
        print(f"Guardado hace:   {(time.time()-d['saved_at'])/3600:.1f}h")

    elif args.cmd == "restore":
        result = mgr.restore(args.port, args.profile, args.page_id)
        print(json.dumps(result, indent=2, ensure_ascii=False))

    elif args.cmd == "list":
        profiles = mgr.list_profiles()
        if not profiles:
            print("No hay perfiles con cache guardado.")
        for p in profiles:
            status = "✓" if p["is_session_valid"] else "✗"
            warn = " ⚠ REWARM" if p["needs_rewarm"] else ""
            print(f"  {status} {p['profile_name']:30s}  sesión: {p['session_expires_in_days']:5.1f}d  CF: {p['cf_clearance_expires_in_days']:5.1f}d{warn}")

    else:
        parser.print_help()