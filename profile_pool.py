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


# Locks por pool (legacy — mantenido para compat). Ya NO se usan como mutex
# por pool; se reemplazaron por locks por puerto (abajo) para permitir que N
# perfiles del mismo pool corran en paralelo.
_pool_locks: dict[str, threading.Lock] = {p: threading.Lock() for p in POOL_URL_PATTERNS}

# Locks por puerto CDP — cada perfil (identificado por su puerto) tiene su
# propio mutex. Dos requests a perfiles distintos corren en paralelo sin
# esperarse. Dos requests al mismo perfil (mismo puerto) se serializan para
# evitar colisiones dentro del navegador (doble click, parseo concurrente).
_port_locks: dict[int, threading.Lock] = {}
_port_locks_mutex = threading.Lock()

# Sem\u00e1foros por pool — limite global de paralelismo para proteger contra
# rate limits del sitio objetivo (ChatGPT, Google Flow). Un sem\u00e1foro con
# valor N permite N operaciones simult\u00e1neas al mismo pool; el lock por
# puerto sigue protegiendo cada perfil individual.
_POOL_SEMAPHORE_LIMITS = {
    "image": 5,  # max 5 requests simult\u00e1neas a ChatGPT
    "video": 3,  # max 3 requests simult\u00e1neas a Google Flow
}
_pool_semaphores: dict[str, threading.BoundedSemaphore] = {
    p: threading.BoundedSemaphore(v) for p, v in _POOL_SEMAPHORE_LIMITS.items()
}


# Cache de descubrimiento (TTL corto). Evita golpear /json en cada request.
_DISCOVERY_TTL_SEC = 3.0
_discovery_lock = threading.Lock()
_last_discovery_at = 0.0
_pool_to_ports: dict[str, list[int]] = {p: [] for p in POOL_URL_PATTERNS}
_port_to_pool: dict[int, str] = {}


# Registry de puertos marcados como "agotados" — perfiles cuya cuenta activa
# se quedo sin tokens y no pueden rotar (OpenAI deshabilito el submenu de
# rotacion). Estos puertos se excluyen de resolve_port() hasta que:
#   - el proceso ginsbrowser correspondiente desaparece (get_running_profiles
#     deja de verlo, auto-limpieza en discover_pools)
#   - alguien llama a unmark_port_exhausted(port) / clear_exhausted_ports()
_exhausted_ports: set[int] = set()
_exhausted_lock = threading.Lock()


def mark_port_exhausted(port: int) -> None:
    """Marca un puerto como agotado. Queda fuera del routing del pool."""
    with _exhausted_lock:
        _exhausted_ports.add(port)


def is_port_exhausted(port: int) -> bool:
    with _exhausted_lock:
        return port in _exhausted_ports


def unmark_port_exhausted(port: int) -> None:
    with _exhausted_lock:
        _exhausted_ports.discard(port)


def clear_exhausted_ports() -> None:
    with _exhausted_lock:
        _exhausted_ports.clear()


def list_exhausted_ports() -> list[int]:
    with _exhausted_lock:
        return sorted(_exhausted_ports)


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
        # Auto-limpieza: si un puerto agotado ya no aparece entre los ginsbrowser
        # corriendo (fue cerrado), liberar su marca para que si vuelve a abrirse
        # (ej: orchestrator abrio otro perfil) no quede bloqueado.
        currently_running = {
            int(p.get("debug_port", 0) or 0) for p in running_profiles
        }
        with _exhausted_lock:
            stale = _exhausted_ports - currently_running
            if stale:
                _exhausted_ports.difference_update(stale)
                log_info(f"[pool] exhausted auto-limpiados (perfiles cerrados): {sorted(stale)}")
        log_info(f"[pool] descubrimiento: {dict(new_map)}")
        return {k: list(v) for k, v in new_map.items()}


def resolve_port(pool: str, running_profiles: list[dict], exclude: set[int] | None = None) -> int:
    """Devuelve un puerto activo del pool, o 0 si no hay disponibles.

    Estrategia:
      1. Intenta con el cache (descubre si esta expirado).
      2. Verifica que el puerto del cache sigue vivo via _test_cdp_port.
      3. Salta puertos marcados como agotados (is_port_exhausted) o en la
         lista `exclude` pasada por el caller (para retries).
      4. Si nada vivo, fuerza un re-descubrimiento.
      5. Si aun nada, retorna 0.
    """
    if pool not in POOL_URL_PATTERNS:
        return 0

    skip = set(exclude or ())

    def _pick(m: dict[str, list[int]]) -> int:
        for p in m.get(pool, []):
            if p in skip:
                continue
            if is_port_exhausted(p):
                continue
            if _test_cdp_port(p):
                return p
        return 0

    mapping = discover_pools(running_profiles, force=False)
    found = _pick(mapping)
    if found:
        return found

    mapping = discover_pools(running_profiles, force=True)
    return _pick(mapping)


def acquire_pool_lock(pool: str) -> threading.Lock:
    """LEGACY — devuelve el lock pesimista del pool.

    Mantiene compat con codigo antiguo. Preferir acquire_port_lock(port)
    para paralelismo real por perfil, y acquire_pool_semaphore(pool) para
    el limite de rate-limit global.
    """
    return _pool_locks[pool]


def acquire_port_lock(port: int) -> threading.Lock:
    """Devuelve el lock por puerto CDP. Usar como context manager:

        with acquire_port_lock(9222):
            ...  # solo una operacion a la vez en ESE perfil

    Perfiles distintos tienen locks distintos → corren en paralelo.
    El lock se crea on-demand la primera vez que se pide un puerto.
    """
    with _port_locks_mutex:
        lock = _port_locks.get(port)
        if lock is None:
            lock = threading.Lock()
            _port_locks[port] = lock
        return lock


def acquire_pool_semaphore(pool: str) -> Optional[threading.BoundedSemaphore]:
    """Devuelve el sem\u00e1foro del pool (limite global de paralelismo).

    Usar en conjunto con acquire_port_lock:

        sem = acquire_pool_semaphore("image")
        with sem, acquire_port_lock(port):
            ...

    Retorna None si el pool no tiene sem\u00e1foro configurado.
    """
    return _pool_semaphores.get(pool)


def known_pools() -> tuple[str, ...]:
    """Lista los pools soportados (utilidad para validacion/logs)."""
    return tuple(POOL_URL_PATTERNS.keys())
