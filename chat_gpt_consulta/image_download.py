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
from chat_gpt_consulta.prompt_paste import ChatGPTSession, get_image_generation_anchor


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


def _check_image_state(session: ChatGPTSession, anchor: dict | None = None) -> dict:
    """Evalúa el estado actual de generación de imagen en ChatGPT.

    Retorna dict con: status, url, width, complete.
    status: GENERATING, COMPARISON_RESOLVED, WAITING, LOADING, READY
    """
    anchor_json = json.dumps(anchor or {})
    js = """(() => {
        const anchor = __ANCHOR_JSON__;
        const normalize = (value) => (value || '')
            .toLowerCase()
            .normalize('NFD')
            .replace(/[\\u0300-\\u036f]/g, '')
            .replace(/\\s+/g, ' ')
            .trim();
        const knownTurnIds = new Set(Array.isArray(anchor.known_turn_ids) ? anchor.known_turn_ids : []);
        const knownImageUrls = new Set(Array.isArray(anchor.known_image_urls) ? anchor.known_image_urls : []);
        const turnIdOf = (turn) =>
            turn.getAttribute('data-turn-id')
            || turn.getAttribute('data-testid')
            || '';
        const isContentImage = (img) => {
            const src = img.currentSrc || img.src || '';
            const w = img.naturalWidth || 0;
            if (w < 100) return false;
            if (src.includes('avatar') || src.includes('icon')) return false;
            return src.includes('/backend-api/')
                || src.includes('oaidalleapi')
                || src.includes('openai.com')
                || src.includes('chatgpt.com')
                || src.includes('blob:')
                || w >= 512;
        };
        const scoreImage = (img) => {
            const alt = normalize(img.alt || '');
            const cls = normalize(img.className || '');
            const parentCls = normalize(img.parentElement?.className || '');
            let score = 0;
            if (alt.includes('imagen generada') || alt.includes('generated image')) score += 4;
            if (!cls.includes('blur') && !parentCls.includes('blur')) score += 2;
            if (img.complete === true) score += 1;
            return score;
        };

        const compBtns = Array.from(document.querySelectorAll('button,[role="button"],label'))
            .filter((el) => /la imagen 1 es mejor|image 1 is better/i.test(
                (el.innerText || el.getAttribute('aria-label') || '').trim()
            ));
        if (compBtns.length > 0) {
            compBtns[0].click();
            return JSON.stringify({status: 'COMPARISON_RESOLVED'});
        }

        const stop = document.querySelector('button[data-testid="stop-button"]')
            || document.querySelector('button[aria-label="Stop generating"]')
            || document.querySelector('button[aria-label="Detener la generación"]');
        const assistantTurns = Array.from(document.querySelectorAll(
            '[data-testid^="conversation-turn"][data-turn="assistant"], section[data-turn="assistant"]'
        ));
        const newAssistantTurns = assistantTurns.filter((turn) => {
            const turnId = turnIdOf(turn);
            return !turnId || !knownTurnIds.has(turnId);
        });
        const candidates = (newAssistantTurns.length > 0 ? newAssistantTurns : assistantTurns)
            .slice(-2)
            .reverse();

        for (const block of candidates) {
            const blockTurnId = turnIdOf(block);
            const blockText = normalize(block.innerText || '');
            const hasWritingBlock = !!block.querySelector('[data-writing-block]');
            const hasFinalizingText = /finalizando imagen|finalizing image|creando imagen|creating image|generando imagen|generating image/.test(blockText);
            const actionReady = !!block.querySelector(
                'button[aria-label="Editar imagen"], '
                + 'button[aria-label="Edit image"], '
                + 'button[data-testid="good-image-turn-action-button"], '
                + 'button[data-testid="bad-image-turn-action-button"]'
            );
            const imgs = Array.from(block.querySelectorAll('img'))
                .filter(isContentImage)
                .map((img) => {
                    const url = img.currentSrc || img.src || '';
                    const w = img.naturalWidth || 0;
                    const h = img.naturalHeight || 0;
                    const complete = img.complete === true;
                    const known = knownImageUrls.has(url);
                    return {
                        url,
                        width: w,
                        height: h,
                        complete,
                        known,
                        score: scoreImage(img),
                    };
                })
                .sort((a, b) => b.score - a.score || b.width - a.width);

            const freshImgs = imgs.filter((img) => !img.known);
            const picked = freshImgs[0] || imgs[0] || null;

            if (!newAssistantTurns.length && picked && picked.known) {
                continue;
            }

            if (hasWritingBlock || hasFinalizingText) {
                if (picked) {
                    return JSON.stringify({
                        status: 'LOADING',
                        turn_id: blockTurnId,
                        url: picked.url,
                        width: picked.width,
                        height: picked.height,
                        complete: picked.complete,
                        action_ready: actionReady,
                        has_finalizing_text: hasFinalizingText,
                    });
                }
                return JSON.stringify({
                    status: stop ? 'GENERATING' : 'WAITING',
                    turn_id: blockTurnId,
                    has_finalizing_text: hasFinalizingText,
                });
            }

            if (picked) {
                return JSON.stringify({
                    status: (picked.width >= 1024 && picked.complete && actionReady) ? 'READY' : 'LOADING',
                    turn_id: blockTurnId,
                    url: picked.url,
                    width: picked.width,
                    height: picked.height,
                    complete: picked.complete,
                    action_ready: actionReady,
                    is_fresh: !picked.known,
                });
            }
        }

        if (stop) {
            return JSON.stringify({status: 'GENERATING'});
        }

        return JSON.stringify({status: 'WAITING'});
    })()"""
    result = session.evaluate(js.replace("__ANCHOR_JSON__", anchor_json))

    if not result:
        return {"status": "WAITING"}
    try:
        return json.loads(result)
    except Exception:
        return {"status": "WAITING"}


# Checks consecutivos con la misma URL para considerar estable.
# DALL-E sirve primero una versión intermedia y luego la final en la misma URL.
_STABLE_CHECKS_REQUIRED = 3
_STABLE_CHECK_INTERVAL = 6  # segundos entre cada check de estabilidad (3 * 6s = 18s max)


def _wait_for_generation_end(session: ChatGPTSession, timeout_sec: int) -> str:
    """Espera sin polling usando MutationObserver a que ChatGPT termine de generar.

    Retorna: 'done', 'rate_limited', 'no_tokens', 'timeout'.
    Un solo CDP evaluate bloqueante — cero polls durante la generación.
    """
    timeout_ms = timeout_sec * 1000
    result = session.evaluate(f"""
    new Promise((resolve) => {{
        const TIMEOUT = {timeout_ms};

        const ratePhrases = [
            'demasiadas solicitudes', 'too many requests', 'rate limit',
            'limitado temporalmente', "you've been temporarily restricted",
        ];
        const tokenPhrases = [
            'has alcanzado tu limite', 'hit the team plan limit', 'hit the plan limit',
            'youve hit', 'the limit resets in', "you've reached your limit",
            'image generation limit', 'cannot generate more images',
        ];

        function checkErrors() {{
            const text = (document.body?.innerText || '').toLowerCase()
                .normalize('NFD').replace(/[\\u0300-\\u036f]/g, '');
            if (ratePhrases.some(p => text.includes(p))) return 'RATE_LIMITED';
            if (tokenPhrases.some(p => text.includes(p))) return 'NO_TOKENS';
            return null;
        }}

        function isGenerating() {{
            return !!(
                document.querySelector('button[data-testid="stop-button"]')
                || document.querySelector('button[aria-label="Stop generating"]')
                || document.querySelector('button[aria-label="Detener la generación"]')
                || document.querySelector('[role="progressbar"]')
            );
        }}

        // Si ya hay error visible, retornar inmediatamente
        const immediate = checkErrors();
        if (immediate) {{ resolve(immediate); return; }}

        // Si ya terminó de generar, retornar inmediatamente
        if (!isGenerating()) {{ resolve('DONE'); return; }}

        let observer = null;
        let timer = null;

        function cleanup() {{
            if (observer) observer.disconnect();
            if (timer) clearTimeout(timer);
        }}

        function done(status) {{ cleanup(); resolve(status); }}

        observer = new MutationObserver(() => {{
            const err = checkErrors();
            if (err) {{ done(err); return; }}
            if (!isGenerating()) {{
                // Esperar 1s para que el DOM se estabilice
                setTimeout(() => {{
                    const err2 = checkErrors();
                    done(err2 || 'DONE');
                }}, 1000);
            }}
        }});

        observer.observe(document.body, {{
            childList: true, subtree: true, attributes: true,
            attributeFilter: ['data-testid', 'aria-label', 'role'],
        }});

        timer = setTimeout(() => {{
            done(checkErrors() || 'TIMEOUT');
        }}, TIMEOUT);
    }})
    """, timeout=timeout_sec + 15, await_promise=True)

    status = str(result or "TIMEOUT").strip()
    return status.lower() if status in ("DONE", "RATE_LIMITED", "NO_TOKENS", "TIMEOUT") else "done"


def wait_for_image(session: ChatGPTSession, timeout_sec: int = 300, anchor: dict | None = None) -> str:
    """Espera que ChatGPT genere la imagen y retorna la URL final estable.

    Fase 1 — MutationObserver (cero polls): espera sin polling a que ChatGPT
              termine de generar. Un único CDP evaluate bloqueante.
    Fase 2 — Polling mínimo: solo para verificar que la URL de imagen es estable
              (DALL-E sirve la misma URL de forma progresiva: primero preview,
              luego versión final). Máximo _STABLE_CHECKS_REQUIRED polls.
    """
    deadline = time.time() + timeout_sec
    log_info("Esperando imagen generada por ChatGPT...")

    # ── Fase 1: MutationObserver — espera sin polling ────────────────────────
    remaining = max(20, int(deadline - time.time()))
    gen_status = _wait_for_generation_end(session, timeout_sec=remaining)

    if gen_status == "rate_limited":
        session.last_error = "rate_limited"
        session._dismiss_rate_limit_modal()
        log_warn("Rate limit detectado durante generación de imagen")
        return ""

    if gen_status == "no_tokens":
        log_warn("Tokens agotados — no hay imagen que esperar")
        return ""

    if gen_status == "timeout":
        log_warn("Timeout esperando que ChatGPT termine la generación")
        return ""

    log_ok("ChatGPT terminó de generar — buscando URL de imagen...")

    # ── Fase 2: Polling mínimo — estabilidad de URL ──────────────────────────
    stable_url = ""
    stable_count = 0

    while time.time() < deadline:
        info = _check_image_state(session, anchor=anchor)
        status = info.get("status", "")

        if status == "COMPARISON_RESOLVED":
            log_info("Comparación de imágenes resuelta, esperando URL...")
            stable_url, stable_count = "", 0
            time.sleep(4)
            continue

        if status == "LOADING":
            w = info.get("width", 0)
            log_info(f"Imagen cargando (width={w}). Esperando...")
            stable_url, stable_count = "", 0
            time.sleep(8)
            continue

        if status == "READY":
            url = info.get("url", "")
            w = info.get("width", 0)
            if url == stable_url:
                stable_count += 1
            else:
                stable_url = url
                stable_count = 1
                log_info(f"Imagen detectada (width={w}). Verificando estabilidad ({stable_count}/{_STABLE_CHECKS_REQUIRED})...")

            if stable_count >= _STABLE_CHECKS_REQUIRED:
                log_ok(f"Imagen final estable: width={w}px ({stable_count} checks)")
                return url

            log_info(f"Check de estabilidad {stable_count}/{_STABLE_CHECKS_REQUIRED}...")
            time.sleep(_STABLE_CHECK_INTERVAL)
            continue

        if status == "GENERATING":
            # Aún generando después del MutationObserver — esperar más
            log_info("Aún generando, esperando...")
            time.sleep(3)
            continue

        # WAITING — imagen aún no apareció tras terminar la generación
        if session._check_rate_limited():
            session.last_error = "rate_limited"
            session._dismiss_rate_limit_modal()
            log_warn("Rate limit detectado tras generación")
            return ""
        if session._check_no_tokens():
            log_warn("Tokens agotados detectados tras generación")
            return ""
        time.sleep(5)

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
# DALL-E genera imágenes finales de 1-4MB. Versiones intermedias pesan 200KB-800KB.
# Solo aceptar imágenes >= 500KB como versión final.
_MIN_FINAL_IMAGE_SIZE = 500_000  # 500KB


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

    # Esperar a que DALL-E termine de servir la versión final en la URL.
    # La URL es la misma pero el contenido cambia: primero preview, luego HQ.
    log_info("Esperando 12s para renderizado final de DALL-E...")
    time.sleep(12)

    cookies = _get_cookies_via_cdp(session)

    for attempt in range(8):
        wait_secs = 5 + attempt * 3  # 5, 8, 11, 14, 17, 20, 23, 26

        log_info(f"Descargando imagen (intento {attempt + 1}/8)...")

        if session._check_rate_limited():
            session.last_error = "rate_limited"
            session._dismiss_rate_limit_modal()
            log_warn("Rate limit detectado antes de descargar la imagen")
            return ""

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
        anchor = get_image_generation_anchor(target_ws) if target_ws else {}
        if anchor:
            log_info(
                "Usando baseline de generación: "
                f"{len(anchor.get('known_turn_ids', []))} turns previos, "
                f"{len(anchor.get('known_image_urls', []))} URLs previas"
            )

        image_url = wait_for_image(session, timeout_sec=timeout, anchor=anchor)
        if not image_url:
            # Determinar si fue por tokens agotados o timeout genérico
            if session.last_error == "rate_limited" or session._check_rate_limited():
                return {
                    "success": False,
                    "error": "rate_limited",
                    "message": "ChatGPT limitó temporalmente el acceso a conversaciones",
                }
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
