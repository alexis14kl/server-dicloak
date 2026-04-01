"""
ChatGPT Image Download — Descarga imágenes generadas por ChatGPT via CDP.

Espera que ChatGPT termine de generar la imagen, obtiene la URL,
y descarga usando fetch() dentro del navegador (con cookies de sesión).

Python puro + CDP WebSocket. Sin Playwright, sin Node.js.
Cross-platform: Windows, Mac, Linux.
"""
from __future__ import annotations

import base64
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from logger import log_info, log_ok, log_warn, log_error
from chat_gpt_consulta.prompt_paste import ChatGPTSession


# Directorio de salida (cross-platform)
DEFAULT_OUTPUT_DIR = Path.home() / "dicloak_images"


def _build_filename(source_url: str) -> str:
    """Genera nombre de archivo a partir de la URL de la imagen."""
    parsed = urlparse(source_url)
    file_id_match = re.search(r"id=([^&]+)", parsed.query or "")
    file_id = file_id_match.group(1) if file_id_match else f"img_{int(time.time())}"
    safe_file_id = re.sub(r"[^a-zA-Z0-9_-]+", "_", file_id)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{timestamp}_{safe_file_id}.png"


def wait_for_image(session: ChatGPTSession, timeout_sec: int = 300) -> str:
    """Espera que ChatGPT genere la imagen y retorna la URL.

    Espera a que ChatGPT termine completamente de generar (stop button desaparece),
    luego espera a que la imagen final de alta resolución se cargue.
    """
    deadline = time.time() + timeout_sec
    log_info("Esperando imagen generada por ChatGPT...")
    generation_done = False

    while time.time() < deadline:
        result = session.evaluate("""(() => {
            // Buscar comparación de imágenes y seleccionar la primera
            const compBtns = Array.from(document.querySelectorAll('button,[role="button"],label'))
                .filter(el => /la imagen 1 es mejor|image 1 is better/i.test(
                    (el.innerText || el.getAttribute('aria-label') || '').trim()
                ));
            if (compBtns.length > 0) {
                compBtns[0].click();
                return JSON.stringify({status: 'COMPARISON_RESOLVED'});
            }

            // Verificar si todavía está generando
            const stop = document.querySelector('button[data-testid="stop-button"]')
                || document.querySelector('button[aria-label="Stop generating"]');
            if (stop) {
                return JSON.stringify({status: 'GENERATING'});
            }

            // Generación terminada — buscar imagen final
            const turns = Array.from(document.querySelectorAll('[data-testid^="conversation-turn"]'));
            const messages = Array.from(document.querySelectorAll('[data-message-id]'));
            const articles = Array.from(document.querySelectorAll('article'));
            const allBlocks = turns.length > 0 ? turns : messages.length > 0 ? messages : articles;
            const candidates = allBlocks.slice(-2).reverse();

            for (const block of candidates) {
                const hasDownload = Array.from(block.querySelectorAll('button,[role="button"],a'))
                    .some(el => /descargar esta imagen|download this image/i.test(
                        (el.innerText || el.getAttribute('aria-label') || '').trim()
                    ));
                const hasOverlay = !!block.querySelector('[data-testid="image-gen-overlay-actions"]');
                const hasCreatedText = /imagen creada|image created/i.test(block.innerText || '');

                if (hasDownload || hasOverlay || hasCreatedText) {
                    const imgs = Array.from(block.querySelectorAll('img'));
                    const urls = imgs
                        .map(img => img.currentSrc || img.src || '')
                        .filter(src => src.includes('/backend-api/estuary/content'));
                    if (urls.length > 0) {
                        // Tomar la última URL (imagen final de mayor resolución)
                        return JSON.stringify({status: 'FOUND', url: urls[urls.length - 1]});
                    }
                }
            }

            return JSON.stringify({status: 'WAITING'});
        })()""")

        if not result:
            time.sleep(2)
            continue

        try:
            info = json.loads(result)
        except Exception:
            time.sleep(2)
            continue

        status = info.get("status", "")

        if status == "GENERATING":
            time.sleep(2)
            continue
        elif status == "COMPARISON_RESOLVED":
            log_info("Comparación de imágenes resuelta, esperando URL...")
            time.sleep(2)
            continue
        elif status == "FOUND":
            if not generation_done:
                # Primera vez que detectamos la imagen — esperar a que se cargue
                # la versión final de alta resolución
                generation_done = True
                log_info("Imagen detectada. Esperando 5s para version final de alta resolucion...")
                time.sleep(5)
                continue  # Volver a buscar para obtener la URL actualizada
            url = info.get("url", "")
            log_ok(f"Imagen final encontrada: {url[:80]}...")
            return url
        else:
            time.sleep(2)

    log_warn("Timeout esperando imagen")
    return ""


def _get_cookies_via_cdp(session: ChatGPTSession) -> str:
    """Extrae cookies de la sesion del navegador via CDP para usar en Python."""
    result = session.evaluate("""(() => document.cookie)()""")
    return result or ""


def _download_with_python(image_url: str, cookies: str, output_path: Path) -> bool:
    """Descarga la imagen directamente desde Python con las cookies del navegador.
    Evita el limite de 4MB del WebSocket CDP.
    """
    import urllib.request

    req = urllib.request.Request(image_url)
    req.add_header("Cookie", cookies)
    req.add_header("User-Agent", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36")

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = resp.read()
            if len(data) < 1000:
                log_warn(f"Imagen muy pequeña ({len(data)} bytes)")
                return False
            output_path.write_bytes(data)
            log_ok(f"Imagen descargada via Python: {output_path} ({len(data)} bytes)")
            return True
    except Exception as e:
        log_warn(f"Descarga Python fallo: {e}")
        return False


def _download_with_cdp(session: ChatGPTSession, image_url: str, output_path: Path) -> bool:
    """Descarga la imagen via fetch() en el navegador (funciona para imagenes < 3MB)."""
    safe_url = image_url.replace("'", "\\'")

    result = session.evaluate(f"""(async () => {{
        try {{
            const resp = await fetch('{safe_url}');
            if (!resp.ok) return JSON.stringify({{error: 'HTTP ' + resp.status}});
            const blob = await resp.blob();
            const reader = new FileReader();
            return await new Promise((resolve) => {{
                reader.onload = () => resolve(JSON.stringify({{
                    base64: reader.result.split(',')[1],
                    size: blob.size,
                    type: blob.type,
                }}));
                reader.readAsDataURL(blob);
            }});
        }} catch(e) {{
            return JSON.stringify({{error: e.message}});
        }}
    }})()""", timeout=60, await_promise=True)

    if not result:
        return False

    try:
        data = json.loads(result)
    except Exception:
        return False

    if data.get("error"):
        log_warn(f"CDP download error: {data['error']}")
        return False

    b64 = data.get("base64", "")
    if not b64 or data.get("size", 0) < 1000:
        return False

    image_bytes = base64.b64decode(b64)
    output_path.write_bytes(image_bytes)
    log_ok(f"Imagen descargada via CDP: {output_path} ({len(image_bytes)} bytes)")
    return True


def download_image(session: ChatGPTSession, image_url: str, output_dir: str = "") -> str:
    """Descarga la imagen generada por ChatGPT.

    Estrategia:
    1. Intenta descargar via Python directo (sin limite de tamaño)
    2. Fallback a CDP fetch (para imagenes pequeñas)

    Returns: ruta del archivo descargado o "" si falla.
    """
    out_dir = Path(output_dir) if output_dir else DEFAULT_OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    filename = _build_filename(image_url)
    output_path = out_dir / filename

    for attempt in range(3):
        log_info(f"Descargando imagen (intento {attempt + 1}/3)...")

        # Estrategia 1: Python directo con cookies del navegador
        cookies = _get_cookies_via_cdp(session)
        if cookies and _download_with_python(image_url, cookies, output_path):
            return str(output_path)

        # Estrategia 2: CDP fetch (funciona si < 4MB)
        if _download_with_cdp(session, image_url, output_path):
            return str(output_path)

        time.sleep(3)

    log_error("No se pudo descargar la imagen después de 3 intentos")
    return ""


def wait_and_download_image(port: int, output_dir: str = "", timeout: int = 300) -> dict:
    """Espera la imagen generada y la descarga.

    Args:
        port: Puerto CDP del navegador con ChatGPT
        output_dir: Directorio de salida (default: ~/dicloak_images)
        timeout: Timeout en segundos para esperar la imagen

    Returns:
        dict con status, file_path, file_size, image_url
    """
    session = ChatGPTSession(port=port)

    if not session.connect():
        return {"success": False, "error": f"No se pudo conectar a ChatGPT en puerto {port}"}

    try:
        # Esperar imagen
        image_url = wait_for_image(session, timeout_sec=timeout)
        if not image_url:
            return {"success": False, "error": "No se generó imagen en el tiempo de espera"}

        # Descargar
        file_path = download_image(session, image_url, output_dir)
        if not file_path:
            return {"success": False, "error": "No se pudo descargar la imagen", "image_url": image_url}

        file_size = Path(file_path).stat().st_size

        return {
            "success": True,
            "file_path": file_path,
            "file_name": Path(file_path).name,
            "file_size": file_size,
            "image_url": image_url,
        }

    finally:
        session.close()
