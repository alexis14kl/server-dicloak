"""Logger simple standalone — reemplaza core.utils.logger."""
import sys


def _safe_print(msg: str) -> None:
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode("utf-8", errors="replace").decode("ascii", errors="replace"))


def log_info(msg: str) -> None:
    _safe_print(f"[INFO] {msg}")


def log_ok(msg: str) -> None:
    _safe_print(f"[OK] {msg}")


def log_warn(msg: str) -> None:
    _safe_print(f"[WARN] {msg}")


def log_error(msg: str) -> None:
    _safe_print(f"[ERROR] {msg}")
