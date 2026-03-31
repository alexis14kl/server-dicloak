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


def os_click_window(hwnd: int, client_x: int, client_y: int) -> bool:
    """Click en una ventana específica via pywinauto (NO mueve el cursor).
    Usa PostMessage (WM_LBUTTONDOWN/UP) directo al HWND.
    """
    try:
        from pywinauto.controls.hwndwrapper import HwndWrapper
        ctrl = HwndWrapper(hwnd)
        ctrl.click(coords=(int(client_x), int(client_y)))
        return True
    except Exception:
        return False


def find_browser_hwnd(screen_x: int) -> int | None:
    """Busca el HWND de la ventana del browser (ginsbrowser/Chrome) por posición X.
    En Windows usa win32gui. Retorna None en otros SO.
    """
    if not IS_WINDOWS:
        return None
    try:
        import win32gui
        result = [None]

        def callback(hwnd, _):
            if not win32gui.IsWindowVisible(hwnd):
                return True
            cls = win32gui.GetClassName(hwnd)
            if cls == "Chrome_WidgetWin_1" and win32gui.GetWindowText(hwnd):
                rect = win32gui.GetWindowRect(hwnd)
                if abs(rect[0] - screen_x) < 50:
                    result[0] = hwnd
                    return False  # stop enumeration
            return True

        win32gui.EnumWindows(callback, None)
        return result[0]
    except Exception:
        return None


def get_visible_hwnds() -> set[int]:
    """Retorna set de HWNDs de ventanas visibles (Windows only)."""
    if not IS_WINDOWS:
        return set()
    try:
        import win32gui
        hwnds = set()

        def callback(hwnd, _):
            if win32gui.IsWindowVisible(hwnd):
                hwnds.add(hwnd)
            return True

        win32gui.EnumWindows(callback, None)
        return hwnds
    except Exception:
        return set()


def find_new_tooltip_hwnd(before_hwnds: set[int]) -> int | None:
    """Busca una ventana nueva tipo Chrome_WidgetWin_* (tooltip de DiCloak).
    Compara con el set de HWNDs antes del click.
    """
    if not IS_WINDOWS:
        return None
    try:
        import win32gui
        after_hwnds = get_visible_hwnds()
        new_hwnds = after_hwnds - before_hwnds

        for hwnd in new_hwnds:
            cls = win32gui.GetClassName(hwnd)
            if "Chrome_WidgetWin" in cls:
                rect = win32gui.GetWindowRect(hwnd)
                w = rect[2] - rect[0]
                h = rect[3] - rect[1]
                if w > 50 and h > 30:
                    return hwnd
        return None
    except Exception:
        return None


def get_window_size(hwnd: int) -> tuple[int, int]:
    """Retorna (width, height) de una ventana por HWND."""
    try:
        import win32gui
        rect = win32gui.GetWindowRect(hwnd)
        return rect[2] - rect[0], rect[3] - rect[1]
    except Exception:
        return 0, 0


def os_click(screen_x: int, screen_y: int, restore_cursor: bool = True) -> bool:
    """Click real del SO en coordenadas absolutas de pantalla (cross-platform).
    Usa pyautogui. Fallback a ctypes/xdotool.
    Solo usar cuando pywinauto (os_click_window) no funciona.
    """
    try:
        import pyautogui
        pyautogui.PAUSE = 0.03

        saved_pos = None
        if restore_cursor:
            saved_pos = pyautogui.position()

        pyautogui.click(int(screen_x), int(screen_y))

        if saved_pos:
            pyautogui.moveTo(saved_pos.x, saved_pos.y, duration=0)

        return True
    except ImportError:
        return _os_click_fallback(int(screen_x), int(screen_y))
    except Exception:
        return False


def _os_click_fallback(screen_x: int, screen_y: int) -> bool:
    """Fallback sin pyautogui."""
    if IS_WINDOWS:
        try:
            import ctypes
            ctypes.windll.user32.SetCursorPos(screen_x, screen_y)
            import time; time.sleep(0.1)
            ctypes.windll.user32.mouse_event(0x02, 0, 0, 0, 0)
            import time; time.sleep(0.05)
            ctypes.windll.user32.mouse_event(0x04, 0, 0, 0, 0)
            return True
        except Exception:
            return False
    else:
        try:
            subprocess.run(
                ["xdotool", "mousemove", str(screen_x), str(screen_y), "click", "1"],
                capture_output=True, timeout=5,
            )
            return True
        except Exception:
            return False


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
