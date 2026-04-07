"""
Extiende un video en Google Flow (Veo 3) y descarga el video completo.

CDP WebSocket directo. Sin Playwright, sin Node.js.
Cross-platform: Windows, Mac, Linux.

Flujo UI de Flow:
1. Galeria del proyecto → tiles generados
2. Click en tile → vista individual (/edit/)
3. Campo de texto abajo ("¿Que pasa despues?" / "What happens next?")
4. Pegar prompt + click →
5. Flow genera extension (clips de 8s individuales)
6. Descarga via menu: Descargar → Full Video → 720p (video completo combinado)
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from logger import log_info, log_ok, log_warn, log_error
from chat_veo3_videos.veo3_session import Veo3Session

DEFAULT_VIDEO_DIR = Path(__file__).resolve().parent / "videos"


# ── Tile: seleccionar video en galeria ──────────────────────────────────────


def _click_tile_to_open(session: Veo3Session) -> bool:
    """Click en el ultimo tile de la galeria para abrir vista individual."""
    log_info("Buscando tiles en la galeria...")
    result = session.evaluate("""(() => {
        const containers = Array.from(document.querySelectorAll(
            'button, [role="button"], a, div[class], figure'
        )).filter(el => {
            if (!el.offsetParent) return false;
            const rect = el.getBoundingClientRect();
            if (rect.width < 100 || rect.height < 80) return false;
            return !!el.querySelector('img, video, canvas, picture');
        });
        if (containers.length) {
            containers[containers.length - 1].click();
            return JSON.stringify({status: 'CLICKED', method: 'container_with_media', count: containers.length});
        }
        const imgs = Array.from(document.querySelectorAll('img')).filter(img => {
            if (!img.offsetParent) return false;
            const rect = img.getBoundingClientRect();
            return rect.width > 150 && rect.height > 100;
        });
        if (imgs.length) {
            imgs[imgs.length - 1].click();
            return JSON.stringify({status: 'CLICKED', method: 'direct_img', count: imgs.length});
        }
        const videos = Array.from(document.querySelectorAll('video')).filter(v => {
            const r = v.getBoundingClientRect();
            return r.width > 50 && r.height > 50 && v.offsetParent !== null;
        });
        if (videos.length) {
            videos[videos.length - 1].click();
            return JSON.stringify({status: 'CLICKED', method: 'direct_video', count: videos.length});
        }
        return JSON.stringify({status: 'NOT_FOUND'});
    })()""")

    if not result:
        log_error("No se pudo evaluar JS para buscar tiles")
        return False
    try:
        info = json.loads(result)
    except Exception:
        log_error(f"Respuesta inesperada: {result}")
        return False

    if info.get("status") == "CLICKED":
        log_ok(f"Tile seleccionado: {info.get('method')} ({info.get('count')} encontrados)")
        return True

    log_error("No se encontro tile para abrir en la galeria")
    return False


# ── Vista individual ────────────────────────────────────────────────────────


def _wait_for_individual_view(session: Veo3Session, timeout_sec: int = 15) -> bool:
    """Espera a que se abra la vista individual (/edit/ o botones Descargar/Hecho)."""
    log_info("Esperando vista individual...")
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        result = session.evaluate("""(() => {
            const url = window.location.href.toLowerCase();
            const hasEdit = url.includes('/edit/');
            const btns = Array.from(document.querySelectorAll('button'));
            const hasActionBtns = btns.some(b => {
                const text = (b.innerText || '').toLowerCase();
                return text.includes('descargar') || text.includes('download')
                    || text.includes('hecho') || text.includes('done');
            });
            const hasField = Array.from(document.querySelectorAll(
                '[contenteditable="true"], [role="textbox"], textarea'
            )).some(el => {
                if (!el.offsetParent) return false;
                const rect = el.getBoundingClientRect();
                return rect.width > 150 && rect.y > window.innerHeight * 0.5;
            });
            if (hasEdit || hasActionBtns || hasField) return 'READY';
            return 'LOADING';
        })()""")
        if result and "READY" in str(result):
            log_ok("Vista individual cargada")
            return True
        time.sleep(1)

    log_error(f"Timeout ({timeout_sec}s): vista individual no cargo")
    return False


# ── Campo de continuacion ───────────────────────────────────────────────────


def _find_and_focus_field(session: Veo3Session, timeout_sec: int = 15) -> bool:
    """Encuentra y enfoca el campo de continuacion (busqueda bilingue)."""
    log_info("Buscando campo de continuacion...")
    deadline = time.time() + timeout_sec

    while time.time() < deadline:
        result = session.evaluate("""(() => {
            const normalizeText = (s) => String(s || '').toLowerCase()
                .normalize('NFD').replace(/[\\u0300-\\u036f]/g, '');
            const needles = [
                'que pasa despues', 'what happens next',
                'que quieres cambiar', 'what do you want to change',
                'que quieres crear', 'what do you want to create'
            ];
            const isVisible = (el) => {
                if (!el || !el.offsetParent) return false;
                const rect = el.getBoundingClientRect();
                return rect.width > 120 && rect.height > 15;
            };
            const editables = Array.from(document.querySelectorAll(
                '[contenteditable="true"], [role="textbox"], textarea'
            )).filter(el => {
                if (!isVisible(el)) return false;
                const rect = el.getBoundingClientRect();
                return rect.width > 150 && rect.y > window.innerHeight * 0.4;
            });
            if (editables.length > 0) {
                const field = editables[0];
                field.scrollIntoView({block: 'center'});
                field.focus();
                field.click();
                const rect = field.getBoundingClientRect();
                return JSON.stringify({
                    status: 'FOUND', method: 'direct_editable',
                    x: rect.x, y: rect.y, w: rect.width, h: rect.height
                });
            }
            const allElements = Array.from(document.querySelectorAll(
                'div, section, span, p, label'
            )).filter(el => {
                if (!isVisible(el)) return false;
                const rect = el.getBoundingClientRect();
                return rect.y > window.innerHeight * 0.4;
            });
            for (const el of allElements) {
                const text = normalizeText(el.innerText || el.textContent || '');
                const placeholder = normalizeText(
                    el.getAttribute('placeholder') || el.getAttribute('aria-label')
                    || el.getAttribute('aria-placeholder') || ''
                );
                const combined = text + ' ' + placeholder;
                if (needles.some(n => combined.includes(n))) {
                    const rect = el.getBoundingClientRect();
                    el.click();
                    const hit = document.elementFromPoint(
                        rect.left + Math.min(rect.width * 0.6, 140),
                        rect.top + rect.height / 2
                    );
                    if (hit) hit.click();
                    return JSON.stringify({
                        status: 'ACTIVATED', method: 'placeholder_click',
                        x: rect.x, y: rect.y, w: rect.width, h: rect.height
                    });
                }
            }
            return JSON.stringify({status: 'NOT_FOUND'});
        })()""")

        if not result:
            time.sleep(1)
            continue
        try:
            info = json.loads(result)
        except Exception:
            time.sleep(1)
            continue

        status = info.get("status", "")

        if status == "FOUND":
            log_ok(f"Campo encontrado ({info.get('method')}) en ({info['x']:.0f},{info['y']:.0f}) {info['w']:.0f}x{info['h']:.0f}")
            return True

        if status == "ACTIVATED":
            log_info("Zona de placeholder activada. Esperando campo editable...")
            time.sleep(1)
            field_check = session.evaluate("""(() => {
                const editables = Array.from(document.querySelectorAll(
                    '[contenteditable="true"], [role="textbox"], textarea'
                )).filter(el => {
                    if (!el.offsetParent) return false;
                    const rect = el.getBoundingClientRect();
                    return rect.width > 150 && rect.y > window.innerHeight * 0.4;
                });
                if (editables.length) {
                    editables[0].focus();
                    editables[0].click();
                    return 'READY';
                }
                return 'NOT_READY';
            })()""")
            if field_check and "READY" in str(field_check):
                log_ok("Campo editable activo")
                return True
            continue

        time.sleep(1)

    log_error(f"Timeout ({timeout_sec}s): campo de continuacion no encontrado")
    return False


# ── Pegar prompt ────────────────────────────────────────────────────────────

# JS reutilizable para encontrar el campo editable
_FIELD_SELECTOR_JS = """Array.from(document.querySelectorAll(
    '[contenteditable="true"], [role="textbox"], textarea'
)).filter(el => {
    if (!el.offsetParent) return false;
    const rect = el.getBoundingClientRect();
    return rect.width > 150 && rect.y > window.innerHeight * 0.4;
})"""


def _paste_extend_prompt(session: Veo3Session, prompt: str) -> bool:
    """Pega el prompt de extension: focus → clear → insertText por chunks."""
    log_info(f"Pegando prompt ({len(prompt)} chars)...")

    if session.evaluate(f"({_FIELD_SELECTOR_JS}).length > 0 ? 'FOUND' : 'NOT_FOUND'") != "FOUND":
        log_error("Campo editable no encontrado para pegar prompt")
        return False

    # Focus
    session.evaluate(f"""(() => {{
        const editor = ({_FIELD_SELECTOR_JS})[0];
        if (!editor) return;
        editor.focus();
        const s = window.getSelection();
        const r = document.createRange();
        r.selectNodeContents(editor);
        r.collapse(false);
        s.removeAllRanges();
        s.addRange(r);
    }})()""")
    time.sleep(0.3)

    # Select all + Backspace
    session.evaluate(f"""(() => {{
        const editor = ({_FIELD_SELECTOR_JS})[0];
        if (!editor) return;
        editor.focus();
        const s = window.getSelection();
        const r = document.createRange();
        r.selectNodeContents(editor);
        s.removeAllRanges();
        s.addRange(r);
    }})()""")
    session._send_raw("Input.dispatchKeyEvent", {
        "type": "keyDown", "key": "Backspace", "code": "Backspace",
        "windowsVirtualKeyCode": 8, "nativeVirtualKeyCode": 8,
    })
    session._send_raw("Input.dispatchKeyEvent", {
        "type": "keyUp", "key": "Backspace", "code": "Backspace",
        "windowsVirtualKeyCode": 8, "nativeVirtualKeyCode": 8,
    })
    time.sleep(0.3)

    # Re-focus
    session.evaluate(f"""(() => {{
        const editor = ({_FIELD_SELECTOR_JS})[0];
        if (!editor) return;
        editor.focus();
        const s = window.getSelection();
        const r = document.createRange();
        r.selectNodeContents(editor);
        r.collapse(false);
        s.removeAllRanges();
        s.addRange(r);
    }})()""")
    time.sleep(0.3)

    # Insert por chunks
    clean_text = prompt.replace('\u201c', "'").replace('\u201d', "'").replace('"', "'")
    CHUNK_SIZE = 200
    for i in range(0, len(clean_text), CHUNK_SIZE):
        chunk = clean_text[i:i + CHUNK_SIZE]
        session._send_raw("Input.insertText", {"text": chunk})
        if i + CHUNK_SIZE < len(clean_text):
            time.sleep(0.05)
    time.sleep(1)

    # Verificar
    content = session.evaluate(f"""(() => {{
        const el = ({_FIELD_SELECTOR_JS})[0];
        if (!el) return '';
        return (el.value || el.innerText || el.textContent || '').trim();
    }})()""") or ""

    placeholders = ['que pasa', 'what happens', 'que quieres', 'what do you']
    is_placeholder = any(p in content.lower() for p in placeholders)

    if len(content) > 5 and not is_placeholder:
        log_ok(f"Prompt pegado y verificado ({len(content)} chars)")
        return True

    log_error(f"Prompt no se registro. Contenido: '{content[:50]}'")
    return False


# ── Boton enviar ────────────────────────────────────────────────────────────


def _click_send_button(session: Veo3Session) -> bool:
    """Click en el boton → (arrow_forward) para enviar."""
    log_info("Buscando boton de envio...")
    result = session.evaluate("""(() => {
        const isVisible = (el) => {
            if (!el) return false;
            const rect = el.getBoundingClientRect();
            return rect.width > 10 && rect.height > 10 && el.offsetParent !== null;
        };
        const buttons = Array.from(document.querySelectorAll('button')).filter(isVisible);
        const scored = buttons.map(btn => {
            const rect = btn.getBoundingClientRect();
            const text = (btn.innerText || btn.textContent || '').toLowerCase();
            const ariaLabel = (btn.getAttribute('aria-label') || '').toLowerCase();
            const skip = ['descargar', 'download', 'hecho', 'done', 'ocultar', 'hide', 'historial', 'history'];
            if (skip.some(k => text.includes(k))) return null;
            if (rect.top < window.innerHeight * 0.5) return null;
            let score = 0;
            if (text.includes('arrow_forward') || text.includes('send') || text.includes('enviar')) score += 500000;
            if (ariaLabel.includes('send') || ariaLabel.includes('submit') || ariaLabel.includes('generate')) score += 400000;
            if (rect.width >= 24 && rect.width <= 60 && rect.height >= 24 && rect.height <= 60) score += 200000;
            if (btn.querySelector('svg, [class*="icon"]')) score += 50000;
            if (score === 0) return null;
            return { score, idx: buttons.indexOf(btn) };
        }).filter(Boolean).sort((a, b) => b.score - a.score);
        if (scored.length) {
            buttons[scored[0].idx].click();
            return 'CLICKED';
        }
        return 'NOT_FOUND';
    })()""")

    if result and "CLICKED" in str(result):
        log_ok("Boton de envio clickeado")
        return True

    log_error("Boton de envio no encontrado")
    return False


# ── Esperar extension ───────────────────────────────────────────────────────


def _check_real_video_exists(session: Veo3Session) -> dict:
    """Verifica si hay videos REALES generados (no placeholders de gstatic).
    Retorna dict con has_video, count, generating.
    """
    result = session.evaluate("""(() => {
        const isPlaceholder = (url) => {
            const v = String(url || '').toLowerCase();
            return v.includes('gstatic.com') || v.includes('aitestkitchen')
                || v.endsWith('/back.mp4') || v.includes('banner');
        };
        const videos = Array.from(document.querySelectorAll('video'));
        const realVideos = videos.filter(v => {
            const src = (v.src || v.currentSrc || '').trim();
            if (!src || isPlaceholder(src)) return false;
            const w = v.videoWidth || 0;
            const h = v.videoHeight || 0;
            if (w > 0 && h > 0 && (w < 320 || h < 180)) return false;
            return true;
        });
        const body = (document.body.innerText || '').toLowerCase();
        const generating = body.includes('generating') || body.includes('generando')
            || body.includes('processing') || body.includes('procesando');
        return JSON.stringify({
            has_video: realVideos.length > 0,
            count: realVideos.length,
            generating: generating,
            total_videos: videos.length,
            real_srcs: realVideos.map(v => (v.src || v.currentSrc || '').substring(0, 80)),
        });
    })()""")
    try:
        return json.loads(result) if result else {"has_video": False, "count": 0, "generating": False}
    except Exception:
        return {"has_video": False, "count": 0, "generating": False}


def _wait_for_real_video(session: Veo3Session, timeout_sec: int = 540) -> bool:
    """Espera que aparezca un video REAL (no placeholder gstatic).
    Retorna True si hay video real, False si timeout o solo placeholders.
    """
    log_info(f"[DESCARGA] Esperando video real (timeout {timeout_sec}s)...")

    # Capturar videos reales conocidos al iniciar
    initial = _check_real_video_exists(session)
    known_count = initial.get("count", 0)
    log_info(f"[DESCARGA] Videos reales al iniciar: {known_count}")

    deadline = time.time() + timeout_sec
    attempt = 0

    while time.time() < deadline:
        attempt += 1
        remaining = int(deadline - time.time())

        info = _check_real_video_exists(session)
        has_video = info.get("has_video", False)
        count = info.get("count", 0)
        generating = info.get("generating", False)

        # Video real nuevo detectado y no esta generando
        if has_video and count > known_count and not generating:
            log_ok(f"[DESCARGA] Video real detectado ({count} videos)")
            return True

        # Si hay video real y no esta generando (para caso de extension ya completada)
        if has_video and not generating and attempt > 10:
            log_ok(f"[DESCARGA] Video real disponible ({count} videos, sin generacion)")
            return True

        if attempt % 5 == 1:
            gen = "generando..." if generating else "esperando..."
            srcs = info.get("real_srcs", [])
            log_info(f"[DESCARGA] [{attempt}] {gen} | reales={count} total={info.get('total_videos', 0)} | quedan {remaining}s")
            if srcs:
                log_info(f"[DESCARGA] URLs reales: {srcs}")

        elapsed = timeout_sec - remaining
        interval = 2 if elapsed < 60 else 3 if elapsed < 300 else 8
        time.sleep(interval)

    log_error(f"[DESCARGA] Timeout ({timeout_sec}s): no se genero video real")
    return False


# ── Descarga via menu UI ────────────────────────────────────────────────────


def _get_cookies_from_cdp(session: Veo3Session) -> str:
    """Extrae cookies (incluidas httpOnly) via CDP Network.getCookies."""
    resp = session._send_raw("Network.getCookies", {"urls": ["https://labs.google"]})
    if not resp:
        return session.evaluate("document.cookie") or ""
    cookies_list = resp.get("result", {}).get("cookies", [])
    return "; ".join(f"{c['name']}={c['value']}" for c in cookies_list)


def _download_video_from_page(session: Veo3Session, output_dir: str = "") -> str:
    """Descarga el video de la vista individual.

    Flujo:
    1. Si no estamos en vista individual, click en tile
    2. Extraer URL del video del elemento <video> en el DOM
    3. Descargar via urllib con cookies httpOnly del browser
    """
    import re
    import urllib.request as _urllib_request

    out_dir = Path(output_dir) if output_dir else DEFAULT_VIDEO_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    # Paso 1: Ir a vista individual si no estamos ahi
    current_url = session.evaluate("window.location.href") or ""
    if "/edit/" not in current_url.lower():
        log_info("[DESCARGA] No estamos en vista individual. Seleccionando tile...")
        if not _click_tile_to_open(session):
            log_error("[DESCARGA] No se pudo abrir tile")
            return ""
        time.sleep(3)
        if not _wait_for_individual_view(session, timeout_sec=15):
            log_error("[DESCARGA] Vista individual no cargo")
            return ""
    else:
        log_info("[DESCARGA] Ya en vista individual")

    time.sleep(2)

    # Paso 2: Extraer URL del video principal del DOM
    log_info("[DESCARGA] Paso 2: Extrayendo URL del video...")
    video_info = session.evaluate("""(() => {
        const isPlaceholder = (url) => {
            const v = String(url || '').toLowerCase();
            return v.includes('gstatic.com') || v.includes('aitestkitchen')
                || v.endsWith('/back.mp4') || v.includes('banner');
        };
        const videos = Array.from(document.querySelectorAll('video'));
        const real = videos.filter(v => {
            const src = (v.src || v.currentSrc || '').trim();
            if (!src || isPlaceholder(src)) return false;
            const w = v.videoWidth || 0;
            const h = v.videoHeight || 0;
            return w >= 320 && h >= 180;
        });
        if (!real.length) return JSON.stringify({found: false});
        // Tomar el video mas grande (principal)
        real.sort((a, b) => (b.videoWidth * b.videoHeight) - (a.videoWidth * a.videoHeight));
        const v = real[0];
        return JSON.stringify({
            found: true,
            src: v.src || v.currentSrc || '',
            width: v.videoWidth || 0,
            height: v.videoHeight || 0,
            duration: isNaN(v.duration) ? 0 : v.duration,
        });
    })()""")

    if not video_info:
        log_error("[DESCARGA] No se pudo evaluar JS para extraer video")
        return ""

    try:
        info = json.loads(video_info)
    except Exception:
        log_error(f"[DESCARGA] Respuesta inesperada: {video_info}")
        return ""

    if not info.get("found"):
        log_error("[DESCARGA] No se encontro video real en la vista individual")
        return ""

    video_url = info["src"]
    width = info.get("width", 0)
    height = info.get("height", 0)
    duration = info.get("duration", 0)
    log_ok(f"[DESCARGA] Video encontrado: {width}x{height} | {duration:.1f}s")
    log_info(f"[DESCARGA] URL: {video_url[:80]}...")

    # Paso 3: Descargar con cookies httpOnly
    log_info("[DESCARGA] Paso 3: Descargando video con cookies...")
    cookies = _get_cookies_from_cdp(session)
    log_info(f"[DESCARGA] Cookies obtenidas: {len(cookies)} chars")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    match = re.search(r"name=([a-f0-9-]+)", video_url)
    video_id = match.group(1)[:12] if match else f"veo_{int(time.time())}"
    output_path = out_dir / f"{ts}_{video_id}.mp4"

    for attempt in range(1, 4):
        log_info(f"[DESCARGA] Intento {attempt}/3...")
        try:
            req = _urllib_request.Request(video_url, headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                "Accept": "video/mp4,video/*;q=0.9,*/*;q=0.8",
                "Cookie": cookies,
                "Referer": "https://labs.google/",
            })
            with _urllib_request.urlopen(req, timeout=180) as resp:
                video_bytes = resp.read()

            if len(video_bytes) < 100_000:
                log_warn(f"[DESCARGA] Intento {attempt}/3: archivo muy pequeno ({len(video_bytes)} bytes)")
                time.sleep(3)
                continue

            output_path.write_bytes(video_bytes)
            size_mb = len(video_bytes) / (1024 * 1024)
            log_ok(f"[DESCARGA] Video descargado: {output_path.name} ({size_mb:.1f}MB)")
            return str(output_path)

        except Exception as e:
            log_warn(f"[DESCARGA] Intento {attempt}/3 fallo: {e}")
            time.sleep(3)

    log_error("[DESCARGA] No se pudo descargar el video despues de 3 intentos")
    return ""


# ── Funciones principales (endpoints) ───────────────────────────────────────


def extend_video(port: int, prompt: str) -> dict:
    """POST /veo3/extend-video — Pega y envia prompt de extension."""
    log_info(f"[EXTENSION] Iniciando extension en puerto {port}")
    log_info(f"[EXTENSION] Prompt: {prompt[:80]}...")

    if not prompt or not prompt.strip():
        log_error("[EXTENSION] Prompt vacio")
        return {"success": False, "error": "El prompt de extension es requerido"}

    session = Veo3Session(port=port)
    if not session.connect():
        log_error(f"[EXTENSION] No se pudo conectar en puerto {port}")
        return {"success": False, "error": f"No se pudo conectar en puerto {port}"}

    try:
        if not session.is_on_flow():
            url = session.evaluate("window.location.href") or ""
            log_error(f"[EXTENSION] No esta en Flow: {url}")
            return {"success": False, "error": "No esta en Flow", "url": url}

        current_url = session.evaluate("window.location.href") or ""
        log_info(f"[EXTENSION] URL actual: {current_url[:80]}")

        in_edit_view = "/edit/" in current_url.lower()

        if not in_edit_view:
            log_info("[EXTENSION] Paso 1: Seleccionando tile de la galeria...")
            if not _click_tile_to_open(session):
                return {"success": False, "error": "No se encontro tile para abrir en la galeria"}
            time.sleep(3)

            log_info("[EXTENSION] Paso 2: Esperando vista individual...")
            if not _wait_for_individual_view(session, timeout_sec=15):
                return {"success": False, "error": "La vista individual no cargo"}
        else:
            log_info("[EXTENSION] Ya en vista individual (/edit/)")

        time.sleep(2)

        log_info("[EXTENSION] Paso 3: Buscando campo de continuacion...")
        if not _find_and_focus_field(session, timeout_sec=15):
            return {"success": False, "error": "No se encontro el campo de continuacion"}
        time.sleep(1)

        log_info("[EXTENSION] Paso 4: Pegando prompt...")
        if not _paste_extend_prompt(session, prompt.strip()):
            return {"success": False, "error": "No se pudo pegar el prompt de extension"}
        time.sleep(3)

        log_info("[EXTENSION] Paso 5: Enviando prompt...")
        if not _click_send_button(session):
            return {"success": False, "error": "No se pudo enviar el prompt"}

        final_url = session.evaluate("window.location.href") or ""
        log_ok(f"[EXTENSION] Prompt enviado exitosamente")

        return {
            "success": True,
            "port": port,
            "url": final_url,
            "prompt_sent": True,
            "prompt_length": len(prompt.strip()),
        }

    finally:
        session.close()


def download_extended_video(port: int, timeout: int = 600, output_dir: str = "") -> dict:
    """POST /veo3/download-video — Espera extension + descarga Full Video."""
    log_info(f"[DESCARGA] Iniciando descarga en puerto {port} (timeout {timeout}s)")

    session = Veo3Session(port=port)
    if not session.connect():
        log_error(f"[DESCARGA] No se pudo conectar en puerto {port}")
        return {"success": False, "error": f"No se pudo conectar en puerto {port}"}

    try:
        log_info("[DESCARGA] Paso 1: Esperando video real...")
        if not _wait_for_real_video(session, timeout_sec=min(timeout, 540)):
            log_error("[DESCARGA] No se detecto video real generado")
            return {"success": False, "error": "No se detecto video real generado", "no_video": True}

        log_info("[DESCARGA] Paso 2: Descargando Full Video via menu UI...")
        file_path = _download_video_from_page(session, output_dir)
        if not file_path:
            return {"success": False, "error": "No se pudo descargar el video via menu UI"}

        file_size = Path(file_path).stat().st_size
        size_mb = file_size / (1024 * 1024)
        log_ok(f"[DESCARGA] Completado: {Path(file_path).name} ({size_mb:.1f}MB)")

        return {
            "success": True,
            "port": port,
            "file_path": file_path,
            "file_name": Path(file_path).name,
            "file_size": file_size,
        }

    finally:
        session.close()
