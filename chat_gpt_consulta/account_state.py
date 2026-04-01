"""
Account State — Estado persistente de cuentas ChatGPT agotadas.

Guarda qué cuentas ya no tienen tokens de imagen con TTL de 4h.
Se auto-limpian las entradas expiradas.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

STATE_FILE = Path(__file__).resolve().parent.parent / ".account_rotation_state.json"
STATE_TTL_SEC = 4 * 60 * 60  # 4 horas


def _load_state() -> dict:
    if not STATE_FILE.exists():
        return {"accounts": {}}
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"accounts": {}}
    if not isinstance(data, dict):
        return {"accounts": {}}
    data.setdefault("accounts", {})
    if not isinstance(data["accounts"], dict):
        data["accounts"] = {}
    return data


def _save_state(state: dict) -> None:
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _cleanup_expired(state: dict) -> dict:
    now = time.time()
    accounts = state.get("accounts", {})
    cleaned = {}
    for key, value in accounts.items():
        if not isinstance(value, dict):
            continue
        ts = float(value.get("ts", 0) or 0)
        if ts and (now - ts) <= STATE_TTL_SEC:
            cleaned[key] = value
    state["accounts"] = cleaned
    return state


def _state_key(port: int, account_id: str) -> str:
    return f"chatgpt:{port}:{account_id}"


def get_exhausted_ids(port: int) -> set[str]:
    """Retorna set de account_ids agotados para este puerto."""
    state = _cleanup_expired(_load_state())
    _save_state(state)
    prefix = f"chatgpt:{port}:"
    return {key[len(prefix):] for key in state["accounts"] if key.startswith(prefix)}


def mark_exhausted(port: int, account_id: str, label: str = "") -> None:
    """Marca una cuenta como agotada."""
    if not account_id:
        return
    state = _cleanup_expired(_load_state())
    state["accounts"][_state_key(port, account_id)] = {
        "label": label,
        "ts": time.time(),
    }
    _save_state(state)


def clear_exhausted(port: int, account_id: str) -> None:
    """Limpia el estado agotado de una cuenta (funcionó con tokens)."""
    if not account_id:
        return
    state = _cleanup_expired(_load_state())
    state["accounts"].pop(_state_key(port, account_id), None)
    _save_state(state)
