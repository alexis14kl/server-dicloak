"""
Account State — Estado persistente de cuentas ChatGPT agotadas.

Centralizado en MySQL (tabla account_rotation), compartida con Django.
Reemplaza el antiguo .account_rotation_state.json.

TTL: 4 horas por cuenta agotada (configurable via env ACCOUNT_ROTATION_TTL_SEC).
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timezone

STATE_TTL_SEC = int(os.environ.get("ACCOUNT_ROTATION_TTL_SEC", str(4 * 60 * 60)))

# ── Conexión MySQL ────────────────────────────────────────────────────────────

def _get_conn():
    """Abre una conexión MySQL usando las variables del .env (ya cargadas por server.py)."""
    import pymysql

    return pymysql.connect(
        host=os.environ.get("DB_HOST", "127.0.0.1"),
        port=int(os.environ.get("DB_PORT", "3306")),
        user=os.environ.get("DB_USER", "root"),
        password=os.environ.get("DB_PASSWORD", ""),
        database=os.environ.get("DB_NAME", "Publicidad"),
        charset="utf8mb4",
        autocommit=True,
        connect_timeout=5,
    )


def _state_key(port: int, account_id: str) -> str:
    return f"chatgpt:{port}:{account_id}"


# ── API pública ───────────────────────────────────────────────────────────────

def get_exhausted_ids(port: int) -> set[str]:
    """Retorna set de account_ids agotados para este puerto (TTL vigente)."""
    prefix = f"chatgpt:{port}:"
    now_dt = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    try:
        conn = _get_conn()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT state_key FROM account_rotation "
                "WHERE state_key LIKE %s AND expires_at > %s",
                (prefix + "%", now_dt),
            )
            rows = cur.fetchall()
        conn.close()
        return {row[0][len(prefix):] for row in rows}
    except Exception as e:
        sys.stderr.write(f"[account_state] get_exhausted_ids error: {e}\n")
        return set()


def mark_exhausted(port: int, account_id: str, label: str = "") -> None:
    """Marca una cuenta como agotada con TTL de STATE_TTL_SEC segundos."""
    if not account_id:
        return
    key = _state_key(port, account_id)
    expires_ts = time.time() + STATE_TTL_SEC
    expires_dt = datetime.fromtimestamp(expires_ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    try:
        conn = _get_conn()
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO account_rotation (state_key, label, expires_at) "
                "VALUES (%s, %s, %s) "
                "ON DUPLICATE KEY UPDATE label=%s, expires_at=%s",
                (key, label or "", expires_dt, label or "", expires_dt),
            )
        conn.close()
    except Exception as e:
        sys.stderr.write(f"[account_state] mark_exhausted error: {e}\n")


def clear_exhausted(port: int, account_id: str) -> None:
    """Elimina el registro de una cuenta que volvió a funcionar."""
    if not account_id:
        return
    key = _state_key(port, account_id)
    try:
        conn = _get_conn()
        with conn.cursor() as cur:
            cur.execute("DELETE FROM account_rotation WHERE state_key = %s", (key,))
        conn.close()
    except Exception as e:
        sys.stderr.write(f"[account_state] clear_exhausted error: {e}\n")


def get_exhausted_ids_fallback(port: int) -> set[str]:
    """Alias de get_exhausted_ids para compatibilidad."""
    return get_exhausted_ids(port)
