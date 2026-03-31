"""Utilidades de plataforma standalone — reemplaza core.cfg.platform.
Cross-platform: Windows, Mac, Linux.
"""
from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")


def get_dicloak_appdata() -> Path:
    """Directorio de datos de DiCloak (cross-platform)."""
    if IS_WINDOWS:
        base = Path(os.environ.get("APPDATA", ""))
    elif IS_MAC:
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "DICloak"


CDP_DEBUG_INFO_JSON = get_dicloak_appdata() / "cdp_debug_info.json"


def read_cdp_debug_info() -> dict:
    """Lee cdp_debug_info.json."""
    if not CDP_DEBUG_INFO_JSON.exists():
        return {}
    try:
        return json.loads(CDP_DEBUG_INFO_JSON.read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_cdp_debug_info(data: dict) -> Path:
    """Escribe cdp_debug_info.json."""
    CDP_DEBUG_INFO_JSON.parent.mkdir(parents=True, exist_ok=True)
    CDP_DEBUG_INFO_JSON.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return CDP_DEBUG_INFO_JSON


def find_dicloak_exe() -> str | None:
    """Busca el ejecutable de DiCloak (cross-platform)."""
    if IS_WINDOWS:
        candidates = [
            Path(os.environ.get("PROGRAMFILES", "")) / "DICloak" / "DICloak.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "DICloak" / "DICloak.exe",
            Path(os.environ.get("PROGRAMFILES(X86)", "")) / "DICloak" / "DICloak.exe",
        ]
    elif IS_MAC:
        candidates = [
            Path("/Applications/DICloak.app/Contents/MacOS/DICloak"),
            Path.home() / "Applications" / "DICloak.app" / "Contents" / "MacOS" / "DICloak",
        ]
    else:
        candidates = [
            Path("/opt/dicloak/dicloak"),
            Path("/usr/local/bin/dicloak"),
            Path.home() / "dicloak" / "dicloak",
        ]

    for p in candidates:
        if p.exists():
            return str(p)
    return None


def launch_detached(cmd: str) -> None:
    """Lanza un proceso desacoplado (cross-platform)."""
    if IS_WINDOWS:
        subprocess.Popen(
            cmd,
            shell=True,
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    else:
        subprocess.Popen(
            cmd,
            shell=True,
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def get_browser_process_name() -> str:
    """Nombre del proceso del navegador de DiCloak."""
    if IS_WINDOWS:
        return "ginsbrowser.exe"
    elif IS_MAC:
        return "ginsbrowser"
    return "ginsbrowser"


def get_process_list() -> list[dict]:
    """Lista de procesos del sistema (cross-platform)."""
    if IS_WINDOWS:
        return _get_process_list_windows()
    return _get_process_list_unix()


def _get_process_list_windows() -> list[dict]:
    try:
        ps_cmd = (
            "Get-CimInstance Win32_Process | "
            "Select-Object ProcessId,Name,CommandLine | "
            "ConvertTo-Json -Compress"
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_cmd],
            capture_output=True, text=True, timeout=15,
            encoding="utf-8", errors="ignore",
        )
        if result.returncode != 0 or not result.stdout.strip():
            return []
        data = json.loads(result.stdout)
        if isinstance(data, dict):
            data = [data]
        return [
            {
                "pid": int(item.get("ProcessId", 0)),
                "name": str(item.get("Name", "")),
                "cmdline": str(item.get("CommandLine", "")),
            }
            for item in data
        ]
    except Exception:
        return []


def _get_process_list_unix() -> list[dict]:
    try:
        result = subprocess.run(
            ["ps", "-eo", "pid,comm,args"],
            capture_output=True, text=True, timeout=10,
        )
        procs = []
        for line in result.stdout.strip().splitlines()[1:]:
            parts = line.strip().split(None, 2)
            if len(parts) >= 2:
                procs.append({
                    "pid": int(parts[0]),
                    "name": parts[1],
                    "cmdline": parts[2] if len(parts) > 2 else "",
                })
        return procs
    except Exception:
        return []
