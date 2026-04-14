"""
profile_pool — Descubrimiento dinamico y enrutamiento de perfiles DICloak por proposito.

Clasifica perfiles activos en pools (image / video) inspeccionando la URL de las
pestañas abiertas en cada navegador via CDP `/json`. No hardcodea nombres de
perfil ni puertos: descubre todo en runtime cada pocos segundos.

Reglas:
    * Un perfil se clasifica en UN solo pool (el primero que machea).
    * Cada pool tiene su propio lock — peticiones del mismo pool se serializan,
      peticiones de pools distintos corren en paralelo.
    * El cache TTL evita golpear /json en cada request.
    * Antes de devolver un puerto, se verifica con _test_cdp_port que sigue vivo.

API:
    discover_pools(running_profiles, force=False) -> dict[pool, list[port]]
    classify_port(port) -> Optional[str]      # nombre del pool o None
    resolve_port(pool, running_profiles) -> int    # puerto vivo del pool, 0 si no hay
    acquire_pool_lock(pool) -> threading.Lock      # lock del pool (usar como context manager)
"""
from __future__ import annotations

import json
import threading
import time
import urllib.request
from typing import Optional

from logger import log_info, log_warn
from cdp_bridge import _test_cdp_port


# Patrones de URL que identifican el proposito de un perfil.
# Si una pestaña del perfil contiene cualquiera de estas substrings en su
# URL (case-insensitive), el perfil se clasifica en ese pool.
#
# IMPORTANTE: NO hardcodea nombres de perfil ni puertos. Solo dominios de
# las apps que el sistema sabe automatizar. Estos dominios provienen de los
# constants de los modulos de automation (chat_gpt_consulta, chat_veo3_videos)
# — son las apps que esos modulos saben manejar.
POOL_URL_PATTERNS: dict[str, tuple[str, ...]] = {
    "image": ("chatgpt.com",),
    "video": ("labs.google", "gemini.google.com"),
}


# Locks por pool — una operacion a la vez por pool. Esto permite que image
# y video corran en paralelo, pero serializa multiples requests del mismo
# flujo (protege rate limiter de chatgpt y evita doble click en la misma
# pestaña del navegador).
_pool_locks: dict[str, threading.Lock] = {p: threading.Lock() for p in POOL_URL_PATTERNS}


# Cache de descubrimiento (TTL corto). Evita golpear /json en cada request.
_DISCOVERY_TTL_SEC = 3.0
_discovery_lock = threading.Lock()
_last_discovery_at = 0.0
_pool_to_ports: dict[str, list[int]] = {p: [] for p in POOL_URL_PATTERNS}
_port_to_pool: dict[int, str] = {}


def _list_targets(port: int) -> list[dict]:
    """Devuelve los targets CDP del puerto, o lista vacia si el puerto no responde."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=2) as r:
            data = json.loads(r.read().decode("utf-8"))
            return data if isinstance(data, list) else []
    except Exception:
        return []


def classify_port(port: int) -> Optional[str]:
    """Clasifica un puerto en un pool segun las URLs de sus pestañas.

    Inspecciona los targets de tipo 'page' del puerto. Si alguna URL machea
    un patron de algun pool, retorna ese pool. Si ninguna machea, retorna None.

    Esta funcion NO usa cache — siempre consulta /json en vivo. Usala con
    moderacion; en hot path usa resolve_port() que cachea via discover_pools.
    """
    targets = _list_targets(port)
    if not targets:
        return None
    for t in targets:
        if t.get("type") != "page":
            continue
        url = (t.get("url") or "").lower()
        if not url:
            continue
        for pool, patterns in POOL_URL_PATTERNS.items():
            if any(p in url for p in patterns):
                return pool
    return None


def discover_pools(running_profiles: list[dict], force: bool = False) -> dict[str, list[int]]:
    """Refresca el mapeo pool→[puertos] consultando /json de cada puerto activo.

    Args:
        running_profiles: salida de DICloakService.get_running_profiles()
        force: si True, ignora el cache TTL y refresca aunque sea reciente.

    Returns:
        dict {pool_name: [puerto, ...]} — copia del estado interno
    """
    global _last_discovery_at, _pool_to_ports, _port_to_pool

    now = time.time()
    with _discovery_lock:
        if not force and (now - _last_discovery_at) < _DISCOVERY_TTL_SEC:
            return {k: list(v) for k, v in _pool_to_ports.items()}

        new_map: dict[str, list[int]] = {p: [] for p in POOL_URL_PATTERNS}
        new_inv: dict[int, str] = {}
        for prof in running_profiles:
            try:
                port = int(prof.get("debug_port", 0) or 0)
            except (TypeError, ValueError):
                continue
            if not port or not prof.get("cdp_active"):
                continue
            pool = classify_port(port)
            if pool:
                new_map[pool].append(port)
                new_inv[port] = pool

        _pool_to_ports = new_map
        _port_to_pool = new_inv
        _last_discovery_at = now
        log_info(f"[pool] descubrimiento: {dict(new_map)}")
        return {k: list(v) for k, v in new_map.items()}


def resolve_port(pool: str, running_profiles: list[dict]) -> int:
    """Devuelve un puerto activo del pool, o 0 si no hay disponibles.

    Estrategia:
      1. Intenta con el cache (descubre si esta expirado).
      2. Verifica que el puerto del cache sigue vivo via _test_cdp_port.
      3. Si nada vivo, fuerza un re-descubrimiento.
      4. Si aun nada, retorna 0.
    """
    if pool not in POOL_URL_PATTERNS:
        return 0

    mapping = discover_pools(running_profiles, force=False)
    for p in mapping.get(pool, []):
        if _test_cdp_port(p):
            return p

    mapping = discover_pools(running_profiles, force=True)
    for p in mapping.get(pool, []):
        if _test_cdp_port(p):
            return p

    return 0


def acquire_pool_lock(pool: str) -> threading.Lock:
    """Devuelve el lock del pool. Usar como context manager:

        with acquire_pool_lock("image"):
            ...  # solo una operacion image a la vez

    Operaciones de pools distintos no se bloquean entre si.
    """
    return _pool_locks[pool]


def known_pools() -> tuple[str, ...]:
    """Lista los pools soportados (utilidad para validacion/logs)."""
    return tuple(POOL_URL_PATTERNS.keys())
