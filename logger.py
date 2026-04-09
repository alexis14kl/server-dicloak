"""Logger simple standalone — reemplaza core.utils.logger.

Incluye un buffer opcional thread-local (`capture_logs`) para que el
endpoint HTTP pueda re-emitir al cliente los logs relevantes de un
proceso concreto (p.ej. el login de Veo3) sin tocar stdout.
"""
import sys
import threading
from contextlib import contextmanager

_tls = threading.local()


def _buffer_append(level: str, msg: str) -> None:
    buf = getattr(_tls, "buffer", None)
    if buf is not None:
        buf.append(f"[{level}] {msg}")


@contextmanager
def capture_logs():
    """Context manager que captura los log_* en un buffer del thread.

    El buffer capturado se devuelve como lista via `get_captured()` dentro
    del bloque `with`, o se puede obtener por el yield. Los logs siguen
    imprimiéndose por stdout normalmente (no es un redirect, es un tee).
    """
    prev = getattr(_tls, "buffer", None)
    buf: list[str] = []
    _tls.buffer = buf
    try:
        yield buf
    finally:
        _tls.buffer = prev


def _safe_print(msg: str) -> None:
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode("utf-8", errors="replace").decode("ascii", errors="replace"))


def log_info(msg: str) -> None:
    _safe_print(f"[INFO] {msg}")
    _buffer_append("INFO", msg)


def log_ok(msg: str) -> None:
    _safe_print(f"[OK] {msg}")
    _buffer_append("OK", msg)


def log_warn(msg: str) -> None:
    _safe_print(f"[WARN] {msg}")
    _buffer_append("WARN", msg)


def log_error(msg: str) -> None:
    _safe_print(f"[ERROR] {msg}")
    _buffer_append("ERROR", msg)
