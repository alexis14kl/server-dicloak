"""
cancellation — Registry en memoria de jobs marcados para abortar.

Este modulo provee un registro thread-safe donde Publicidad (via el
endpoint POST /jobs/{job_id}/cancel) marca jobs que deben detenerse.
Los loops internos de automation (wait_and_download_image,
paste_and_send_prompt, download_extended_video, etc.) consultan este
registry entre iteraciones y cortan limpiamente cuando detectan la
bandera.

Politica:
    * Thread-safe: usa lock interno. Todos los endpoints de FastAPI
      corren en hilos del pool, por lo que el set puede ser modificado
      y leido desde threads distintos.
    * En memoria: se pierde al reiniciar server_dicloak. Es aceptable
      porque los jobs activos en ese momento tambien se pierden
      (server_dicloak es stateless respecto a jobs de Publicidad).
    * Sin TTL automatico: el caller debe llamar a `clear(job_id)`
      cuando el flujo termina para evitar crecimiento indefinido.
      Los helpers de los flujos principales hacen esto al final.

API publica:
    mark_cancelled(job_id)   — flipea la bandera (llamado por el endpoint)
    is_cancelled(job_id)     — consulta (llamado por los loops)
    clear(job_id)            — limpia la bandera (llamado al terminar)
    check_and_raise(job_id)  — shortcut: levanta JobCancelled si esta marcado
    list_cancelled()         — debug/introspection

Excepcion:
    JobCancelled — se usa para propagar la cancelacion por la pila sin
    enredar el flujo normal con returns condicionales. Los endpoints
    FastAPI la capturan al final y devuelven un error_response claro.
"""
from __future__ import annotations

import threading
from typing import Set


class JobCancelled(Exception):
    """Se levanta cuando un loop detecta que su job fue cancelado.

    Los endpoints FastAPI deben capturar esta excepcion en su try/except
    final y retornar una respuesta con `error='cancelled'` y status 499
    (codigo no estandar pero usado por nginx/cliente para "client closed
    request"). Publicidad interpreta este codigo como cancelacion limpia.
    """


# ─── Estado interno ───────────────────────────────────────────────────────
_cancelled: Set[str] = set()
_lock = threading.Lock()


def mark_cancelled(job_id: str) -> bool:
    """Marca un job como cancelado. Thread-safe.

    Returns:
        True si la bandera se anadio, False si ya estaba marcada.
    """
    if not job_id:
        return False
    with _lock:
        if job_id in _cancelled:
            return False
        _cancelled.add(job_id)
        return True


def is_cancelled(job_id: str) -> bool:
    """Consulta si un job fue marcado para cancelar. Thread-safe.

    Los loops de espera llaman esto cada iteracion (ej: cada 1-2s).
    Si retorna True, el loop debe romper y limpiar.
    """
    if not job_id:
        return False
    with _lock:
        return job_id in _cancelled


def clear(job_id: str) -> bool:
    """Quita la bandera de cancelacion. Thread-safe.

    Se llama al terminar el flujo (exitoso, error o cancelado) para
    que el set no crezca indefinidamente.

    Returns:
        True si se removio, False si no estaba marcado.
    """
    if not job_id:
        return False
    with _lock:
        if job_id not in _cancelled:
            return False
        _cancelled.discard(job_id)
        return True


def check_and_raise(job_id: str) -> None:
    """Shortcut: si el job esta cancelado, levanta JobCancelled.

    Los loops pueden usar esto en vez de `if is_cancelled(): return ...`
    para desenrollar la pila limpiamente y dejar que el endpoint
    FastAPI centralice el manejo del error en un solo try/except.
    """
    if is_cancelled(job_id):
        raise JobCancelled(f"Job {job_id} cancelado por el cliente")


def list_cancelled() -> list[str]:
    """Lista los jobs marcados (debug / introspection)."""
    with _lock:
        return sorted(_cancelled)


# ─── Shutdown cooperativo del server ─────────────────────────────────────
# Cuando uvicorn recibe Ctrl+C dispara el lifespan shutdown, que llama a
# request_shutdown(). Los loops largos (image_download fase 2, etc.) usan
# interruptible_sleep() en lugar de time.sleep() para reaccionar en cuanto
# el flag se activa, en vez de quedar bloqueados hasta que uvicorn los mata
# por timeout_graceful_shutdown.
_shutdown_event = threading.Event()


def request_shutdown() -> None:
    """Marca el server como apagandose. Despierta cualquier interruptible_sleep."""
    _shutdown_event.set()


def is_shutting_down() -> bool:
    return _shutdown_event.is_set()


def interruptible_sleep(seconds: float, job_id: str = "") -> None:
    """Sleep cooperativo: respeta shutdown del server y cancelacion del job.

    Reemplaza directamente a `time.sleep(seconds)` en los loops de polling.
    Si durante el sleep:
      - se activa el shutdown del server, levanta JobCancelled("shutdown")
      - el job esta marcado para cancelar, levanta JobCancelled(...)
    Si no, retorna normalmente al cumplirse el tiempo.
    """
    if seconds <= 0:
        if job_id:
            check_and_raise(job_id)
        return
    if _shutdown_event.wait(timeout=seconds):
        raise JobCancelled("server shutting down")
    if job_id:
        check_and_raise(job_id)
