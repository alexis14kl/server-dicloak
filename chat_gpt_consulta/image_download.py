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


def _check_image_state(session: ChatGPTSession) -> dict:
    """Evalúa el estado actual de generación de imagen en ChatGPT.

    Retorna dict con: status, url, width, complete.
    status: GENERATING, COMPARISON_RESOLVED, WAITING, LOADING, READY
    """
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

        // Generación terminada — buscar imagen
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
                const imgs = Array.from(block.querySelectorAll('img'))
                    .filter(img => (img.currentSrc || img.src || '').includes('/backend-api/'));
                if (imgs.length > 0) {
                    const img = imgs[imgs.length - 1];
                    const url = img.currentSrc || img.src;
                    const w = img.naturalWidth || 0;
                    const h = img.naturalHeight || 0;
                    const complete = img.complete === true;
                    return JSON.stringify({
                        status: (w > 512 && complete) ? 'READY' : 'LOADING',
                        url, width: w, height: h, complete
                    });
                }
            }
        }

        return JSON.stringify({status: 'WAITING'});
    })()""")

    if not result:
        return {"status": "WAITING"}
    try:
        return json.loads(result)
    except Exception:
        return {"status": "WAITING"}


# Cantidad de checks consecutivos con la misma URL para considerar estable
_STABLE_CHECKS_REQUIRED = 4
_STABLE_CHECK_INTERVAL = 2  # segundos entre cada check de estabilidad


def wait_for_image(session: ChatGPTSession, timeout_sec: int = 300) -> str:
    """Espera que ChatGPT genere la imagen y retorna la URL final estable.

    La imagen de ChatGPT pasa por varias fases:
      1. GENERATING — el stop button está visible
      2. LOADING — la imagen aparece pero aún carga (baja resolución / progresiva)
      3. READY — img.complete=true y naturalWidth > 512

    Después de READY, esperamos que la URL sea estable (no cambie) durante
    varios checks consecutivos, para asegurar que es la versión final
    y no un preview intermedio.
    """
    deadline = time.time() + timeout_sec
    log_info("Esperando imagen generada por ChatGPT...")

    # Verificación inicial: ¿hay algo generándose o ya generado?
    no_activity_count = 0
    max_idle_checks = 3  # ~3 segundos sin actividad → abortar
    for _ in range(max_idle_checks):
        initial = _check_image_state(session)
        initial_status = initial.get("status", "")
        if initial_status in ("GENERATING", "LOADING", "READY", "COMPARISON_RESOLVED"):
            log_info(f"Actividad detectada: {initial_status}")
            break
        if session._check_no_tokens():
            log_warn("Tokens agotados — no hay imagen que esperar")
            return ""
        no_activity_count += 1
        if no_activity_count >= max_idle_checks:
            log_warn("No hay imagen generándose en ChatGPT. Abortando espera.")
            return ""
        time.sleep(1)

    stable_url = ""
    stable_count = 0
    token_check_counter = 0

    while time.time() < deadline:
        # Cada 5 ciclos, verificar si ChatGPT respondió con error de tokens
        # en vez de generar una imagen (evita esperar 300s en vano)
        token_check_counter += 1
        if token_check_counter % 5 == 0:
            if session._check_no_tokens():
                log_warn("Tokens agotados detectados durante espera de imagen")
                return ""

        info = _check_image_state(session)
        status = info.get("status", "")

        if status == "GENERATING":
            stable_url, stable_count = "", 0
            time.sleep(2)
            continue

        if status == "COMPARISON_RESOLVED":
            log_info("Comparación de imágenes resuelta, esperando URL...")
            stable_url, stable_count = "", 0
            time.sleep(2)
            continue

        if status == "LOADING":
            w = info.get("width", 0)
            log_info(f"Imagen cargando (width={w}, complete={info.get('complete')}). Esperando...")
            stable_url, stable_count = "", 0
            time.sleep(3)
            continue

        if status == "READY":
            url = info.get("url", "")
            w = info.get("width", 0)

            if url == stable_url:
                stable_count += 1
            else:
                # URL cambió — reiniciar contador de estabilidad
                stable_url = url
                stable_count = 1
                log_info(f"Imagen detectada (width={w}). Verificando estabilidad...")

            if stable_count >= _STABLE_CHECKS_REQUIRED:
                log_ok(f"Imagen final estable: width={w}px ({stable_count} checks)")
                return url

            time.sleep(_STABLE_CHECK_INTERVAL)
            continue

        # WAITING — no hay imagen aún, chequear tokens más frecuente
        stable_url, stable_count = "", 0
        if session._check_no_tokens():
            log_warn("Tokens agotados detectados durante espera de imagen")
            return ""
        time.sleep(2)

    log_warn("Timeout esperando imagen")
    return ""


def _get_cookies_via_cdp(session: ChatGPTSession) -> str:
    """Extrae TODAS las cookies (incluidas HttpOnly) via CDP Network.getCookies."""
    try:
        resp = session._send_raw("Network.getCookies", {"urls": ["https://chatgpt.com"]})
        if resp and "result" in resp:
            cookies = resp["result"].get("cookies", [])
            return "; ".join(f"{c['name']}={c['value']}" for c in cookies)
    except Exception:
        pass
    # Fallback a document.cookie (no incluye HttpOnly)
    result = session.evaluate("""(() => document.cookie)()""")
    return result or ""


def _download_with_python(image_url: str, cookies: str, output_path: Path) -> bool:
    """Descarga la imagen directamente desde Python con las cookies del navegador.
    Evita el limite de 4MB del WebSocket CDP.
    """
    import ssl
    import urllib.request

    # Crear contexto SSL que no verifique certificados (ChatGPT usa cert propio)
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE

    req = urllib.request.Request(image_url)
    req.add_header("Cookie", cookies)
    req.add_header("User-Agent", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36")

    try:
        with urllib.request.urlopen(req, timeout=15, context=ssl_ctx) as resp:
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


# Tamaño mínimo para considerar una imagen como final (no preview borroso).
# ChatGPT genera imágenes de 200KB-3MB dependiendo del contenido.
# Previews borrosos pesan < 100KB. Imágenes reales >= 200KB.
_MIN_FINAL_IMAGE_SIZE = 200_000  # 200KB


def download_image(session: ChatGPTSession, image_url: str, output_dir: str = "") -> str:
    """Descarga la imagen FINAL generada por ChatGPT.

    ChatGPT sirve la misma URL con calidad progresiva:
      - Primero: preview borroso (~100-500KB)
      - Después: imagen final (~1.5-3MB)

    Estrategia: descarga, verifica tamaño >= 800KB, si no espera y reintenta.
    Hasta 8 intentos con espera creciente.

    Returns: ruta del archivo descargado o "" si falla.
    """
    out_dir = Path(output_dir) if output_dir else DEFAULT_OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    filename = _build_filename(image_url)
    output_path = out_dir / filename

    # Esperar antes del primer intento para dar tiempo a la renderización final
    log_info("Esperando 10s para que ChatGPT finalice el renderizado...")
    time.sleep(10)

    cookies = _get_cookies_via_cdp(session)

    for attempt in range(8):
        wait_secs = 5 + attempt * 3  # 5, 8, 11, 14, 17, 20, 23, 26

        log_info(f"Descargando imagen (intento {attempt + 1}/8)...")

        # Re-obtener cookies si no las tenemos
        if not cookies:
            cookies = _get_cookies_via_cdp(session)

        if cookies and _download_with_python(image_url, cookies, output_path):
            file_size = output_path.stat().st_size
            if file_size >= _MIN_FINAL_IMAGE_SIZE:
                log_ok(f"Imagen final descargada: {file_size:,} bytes")
                return str(output_path)
            log_warn(f"Preview descargado ({file_size:,} bytes < {_MIN_FINAL_IMAGE_SIZE:,}). "
                     f"Esperando {wait_secs}s para version final...")
            output_path.unlink(missing_ok=True)
        else:
            log_warn(f"Descarga falló. Esperando {wait_secs}s...")

        time.sleep(wait_secs)

        # Re-verificar si la URL cambió
        info = _check_image_state(session)
        new_url = info.get("url", "")
        if new_url and new_url != image_url:
            log_info("URL de imagen actualizada, usando nueva URL")
            image_url = new_url
            filename = _build_filename(image_url)
            output_path = out_dir / filename
            cookies = _get_cookies_via_cdp(session)  # refrescar cookies

    log_error("No se pudo descargar la imagen final después de 8 intentos")
    return ""


def wait_and_download_image(port: int, output_dir: str = "", timeout: int = 300,
                            target_ws: str = "") -> dict:
    """Espera la imagen generada y la descarga.

    Args:
        port: Puerto CDP del navegador con ChatGPT
        output_dir: Directorio de salida (default: ~/dicloak_images)
        timeout: Timeout en segundos para esperar la imagen
        target_ws: WebSocket URL exacta de la tab donde se envio el prompt.
                   Si se pasa, se conecta directo a esa tab sin buscar.

    Returns:
        dict con status, file_path, file_size, image_url
    """
    session = ChatGPTSession(port=port)

    if target_ws:
        # Conectar directo a la tab que genero la imagen
        session.ws_url = target_ws
        try:
            import websockets.sync.client as ws_sync
            session._ws = ws_sync.connect(target_ws, max_size=2**22)
            log_ok(f"Conectado directo a tab del prompt: {target_ws[-40:]}")
        except Exception as e:
            log_warn(f"No se pudo conectar a target_ws, buscando tab: {e}")
            if not session.connect():
                return {"success": False, "error": f"No se pudo conectar a ChatGPT en puerto {port}"}
    elif not session.connect():
        return {"success": False, "error": f"No se pudo conectar a ChatGPT en puerto {port}"}

    try:
        # Esperar imagen
        image_url = wait_for_image(session, timeout_sec=timeout)
        if not image_url:
            # Determinar si fue por tokens agotados o timeout genérico
            if session._check_no_tokens():
                return {
                    "success": False,
                    "error": "all_accounts_exhausted",
                    "message": "Tokens de imagen agotados en este perfil",
                }
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
