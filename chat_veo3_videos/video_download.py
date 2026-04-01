"""
Download Video — Espera y descarga el video generado en Google Flow (Veo 3).

Python puro + CDP WebSocket. Sin Playwright, sin Node.js.
Cross-platform: Windows, Mac, Linux.

Flujo:
1. Conectar al puerto CDP del perfil
2. Polling: esperar a que aparezca un <video> con readyState >= 3
3. Descargar via fetch+blob+base64 (browser tiene cookies) o urllib directo
4. Guardar en output_dir/{timestamp}_{id}.mp4
"""
from __future__ import annotations

import base64
import json
import os
import re
import sys
import time
import urllib.request as _urllib
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from logger import log_info, log_ok, log_warn, log_error
from chat_veo3_videos.veo3_session import Veo3Session


DEFAULT_TIMEOUT_SEC = 600   # 10 minutos
MAX_DOWNLOAD_RETRIES = 3

# JS para detectar video listo en Flow
_POLL_VIDEO_JS = """(previousVideoUrl) => {
    const videos = Array.from(document.querySelectorAll('video'));
    const buttons = Array.from(document.querySelectorAll('button'));
    const hasDownload = buttons.some(b => {
        const text = (b.innerText || '').toLowerCase();
        return (text.includes('descargar') || text.includes('download')) && b.offsetParent !== null;
    });
    const isPlaceholder = (url) => {
        const value = String(url || '').toLowerCase();
        return value.includes('/back.mp4') || value.endsWith('/back.mp4');
    };

    const withSrc = videos.filter(v => (v.src || v.currentSrc));
    const ready = withSrc.filter(v => {
        const current = (v.src || v.currentSrc || '').trim();
        const width = v.videoWidth || 0;
        const height = v.videoHeight || 0;
        const duration = isNaN(v.duration) ? 0 : v.duration;
        if (!current || isPlaceholder(current)) return false;
        if (!(v.readyState >= 3 || hasDownload)) return false;
        if (width > 0 && height > 0 && (width < 320 || height < 180)) return false;
        if (duration > 0 && duration < 2) return false;
        return true;
    });

    const previous = (previousVideoUrl || '').trim();
    const newReady = ready.filter(v => {
        const current = (v.src || v.currentSrc || '').trim();
        return current && current !== previous;
    });

    if (newReady.length === 0) {
        const body = (document.body.innerText || '').toLowerCase();
        const isGenerating = body.includes('generando') || body.includes('generating')
            || body.includes('procesando') || body.includes('processing')
            || body.includes('en cola') || body.includes('queued');
        return JSON.stringify({
            found: false,
            totalVideos: videos.length,
            videosWithSrc: withSrc.length,
            readyVideos: ready.length,
            hasDownload: hasDownload,
            isGenerating: isGenerating,
        });
    }
    const v = newReady[newReady.length - 1];
    return JSON.stringify({
        found: true,
        src: v.src || v.currentSrc || '',
        width: v.videoWidth || 0,
        height: v.videoHeight || 0,
        duration: isNaN(v.duration) ? 0 : v.duration,
        readyState: v.readyState,
        hasDownload: hasDownload,
    });
}"""

# JS para obtener la URL real del video (sigue redirect, retorna URL de GCS)
_RESOLVE_VIDEO_URL_JS = """(url) => {
    return fetch(url, {redirect: 'follow'})
        .then(r => {
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return JSON.stringify({
                resolvedUrl: r.url,
                size: parseInt(r.headers.get('content-length') || '0'),
                type: r.headers.get('content-type') || '',
                status: r.status,
            });
        });
}"""


def _poll_for_video(session: Veo3Session, timeout_sec: int, previous_url: str = "") -> dict | None:
    """Polling: espera a que aparezca un video listo."""
    log_info(f"Esperando video generado (timeout {timeout_sec}s)...")
    deadline = time.time() + timeout_sec
    attempt = 0

    while time.time() < deadline:
        attempt += 1
        elapsed = int(time.time() - (deadline - timeout_sec))
        remaining = int(deadline - time.time())

        if not session.is_connected():
            session.connect()

        # Verificar error de página
        has_error = session.evaluate("(document.body?.innerText || '').includes('Application error')")
        if has_error:
            log_error("La página crasheó (Application error)")
            return None

        # Buscar videos
        raw = session.evaluate(f"({_POLL_VIDEO_JS})('{previous_url}')")
        if not raw:
            time.sleep(2)
            continue

        try:
            info = json.loads(raw)
        except Exception:
            time.sleep(2)
            continue

        if info.get("found"):
            w = info.get("width", 0)
            h = info.get("height", 0)
            dur = info.get("duration", 0)
            rs = info.get("readyState", 0)
            log_ok(f"Video listo! {w}x{h} | {dur:.1f}s | readyState={rs}")

            # Si no está completamente buffered, esperar un poco más
            if rs < 4 and remaining > 15:
                log_info("Esperando buffer completo (10s)...")
                time.sleep(10)

            return info

        # Log status cada 3 intentos
        if attempt % 3 == 1:
            total = info.get("totalVideos", 0)
            with_src = info.get("videosWithSrc", 0)
            generating = info.get("isGenerating", False)
            status = "generando..." if generating else f"{total} videos ({with_src} con src)"
            log_info(f"  [{attempt}] {status} | quedan {remaining}s")

        # Adaptive polling
        interval = 2 if elapsed < 60 else 3 if elapsed < 300 else 8
        time.sleep(interval)

    log_error(f"Timeout ({timeout_sec}s): no se generó ningún video.")
    return None


def _resolve_video_url(session: Veo3Session, video_url: str) -> str | None:
    """Resuelve la URL del video siguiendo redirects dentro del browser (tiene cookies).
    Retorna la URL real de Google Cloud Storage.
    """
    try:
        raw = session.evaluate(f"({_RESOLVE_VIDEO_URL_JS})('{video_url}')",
                               timeout=30, await_promise=True)
        if not raw:
            return None
        data = json.loads(raw)
        resolved = data.get("resolvedUrl", "")
        if resolved and "storage.googleapis.com" in resolved:
            log_ok(f"URL resuelta: {resolved[:80]}...")
            return resolved
        log_info(f"URL resuelta (no GCS): {resolved[:80]}")
        return resolved or None
    except Exception as e:
        log_warn(f"No se pudo resolver URL: {e}")
        return None


def _download_video_direct(video_url: str) -> bytes | None:
    """Descarga directa via urllib (sin browser, sin cookies)."""
    for attempt in range(1, MAX_DOWNLOAD_RETRIES + 1):
        try:
            req = _urllib.Request(video_url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "video/mp4,video/*;q=0.9,*/*;q=0.8",
            })
            with _urllib.urlopen(req, timeout=180) as resp:
                video_bytes = resp.read()

            if len(video_bytes) < 100_000:
                log_warn(f"Descarga directa intento {attempt}: muy pequeño ({len(video_bytes)} bytes)")
                time.sleep(3)
                continue

            size_mb = len(video_bytes) / (1024 * 1024)
            log_ok(f"Video descargado via HTTP directo ({size_mb:.1f}MB)")
            return video_bytes

        except Exception as e:
            log_warn(f"Descarga directa intento {attempt}/{MAX_DOWNLOAD_RETRIES}: {e}")
            time.sleep(3)

    return None


def download_video(port: int, output_dir: str = "", timeout: int = DEFAULT_TIMEOUT_SEC) -> dict:
    """
    Espera a que el video se genere en Flow y lo descarga.

    Args:
        port: Puerto CDP del perfil
        output_dir: Directorio de salida (default: ~/dicloak_videos)
        timeout: Timeout en segundos para esperar generación

    Returns:
        dict con success, file_path, video_url, etc.
    """
    session = Veo3Session(port=port)
    if not session.connect():
        return {"success": False, "error": f"No se pudo conectar en puerto {port}"}

    try:
        # Verificar que estamos en un proyecto de Flow
        url = (session.evaluate("window.location.href") or "").lower()
        if "/project/" not in url:
            return {"success": False, "error": "No está en un proyecto de Flow", "url": url}

        # Polling por video
        video_info = _poll_for_video(session, timeout_sec=timeout)
        if not video_info:
            return {"success": False, "error": "No se generó video en el tiempo límite"}

        video_url = video_info.get("src", "")
        if not video_url:
            return {"success": False, "error": "Video encontrado pero sin URL"}

        # Directorio de salida
        if not output_dir:
            output_dir = str(Path.home() / "dicloak_videos")
        Path(output_dir).mkdir(parents=True, exist_ok=True)

        # Nombre del archivo
        match = re.search(r"name=([a-f0-9-]+)", video_url)
        video_id = match.group(1)[:12] if match else f"veo_{int(time.time())}"
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{timestamp}_{video_id}.mp4"
        output_path = Path(output_dir) / filename

        # Resolver URL real (seguir redirect dentro del browser con cookies)
        resolved_url = _resolve_video_url(session, video_url)
        download_url = resolved_url or video_url

        # Descargar con urllib directo (la URL de GCS no necesita cookies)
        video_bytes = _download_video_direct(download_url)
        if not video_bytes and resolved_url:
            log_info("Descarga de URL resuelta falló, intentando URL original...")
            video_bytes = _download_video_direct(video_url)

        if not video_bytes:
            return {
                "success": False,
                "error": "No se pudo descargar el video",
                "video_url": video_url,
            }

        output_path.write_bytes(video_bytes)
        size_mb = len(video_bytes) / (1024 * 1024)

        return {
            "success": True,
            "file_path": str(output_path),
            "video_url": video_url,
            "size_mb": round(size_mb, 1),
            "width": video_info.get("width", 0),
            "height": video_info.get("height", 0),
            "duration": round(video_info.get("duration", 0), 1),
        }

    finally:
        session.close()
