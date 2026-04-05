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


def _download_full_video(session: Veo3Session, output_dir: str = "") -> str:
    """Descarga video via menu UI de la vista individual.

    Flujo:
    1. Click en tile para abrir vista individual (/edit/)
    2. En vista individual: click en boton "Descargar"/"Download" de la barra superior
    3. Click en opcion de descarga (Full Video → 720p, o descarga directa)
    4. Esperar archivo en directorio
    """
    out_dir = Path(output_dir) if output_dir else DEFAULT_VIDEO_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    # Configurar CDP para descargas
    log_info(f"[DESCARGA] Configurando directorio: {out_dir}")
    session._send_raw("Browser.setDownloadBehavior", {
        "behavior": "allow",
        "downloadPath": str(out_dir),
    })
    session._send_raw("Page.setDownloadBehavior", {
        "behavior": "allow",
        "downloadPath": str(out_dir),
    })

    existing_files = set(f.name for f in out_dir.glob("*.*"))
    log_info(f"[DESCARGA] Archivos existentes: {len(existing_files)}")

    # Paso 1: Verificar si estamos en vista individual, si no, ir ahi
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

    time.sleep(1)

    # Paso 2: Click en boton "Descargar"/"Download" de la BARRA SUPERIOR
    # En la vista individual los botones estan arriba: "Descargar", "Ocultar historial", "Hecho"
    log_info("[DESCARGA] Paso 2: Click en Descargar (barra superior)...")
    clicked_download = session.evaluate("""(() => {
        const btns = Array.from(document.querySelectorAll('button'));
        // Buscar boton que diga EXACTAMENTE "Descargar" o "Download"
        // (con posible icono "download" antes)
        for (const btn of btns) {
            if (!btn.offsetParent) continue;
            const rect = btn.getBoundingClientRect();
            // Debe estar en la parte SUPERIOR (barra de acciones)
            if (rect.top > 200) continue;
            const text = (btn.innerText || '').trim().toLowerCase();
            if (text.includes('descargar') || text.includes('download')) {
                btn.click();
                return 'CLICKED: ' + (btn.innerText || '').trim().substring(0, 30);
            }
        }
        return 'NOT_FOUND';
    })()""")

    if not clicked_download or "CLICKED" not in str(clicked_download):
        log_error("[DESCARGA] No se encontro boton Descargar en barra superior")
        return ""
    log_ok(f"[DESCARGA] {clicked_download}")
    time.sleep(2)

    # Debug: que aparecio despues del click
    menu_debug = session.evaluate("""(() => {
        // Buscar elementos de menu/popup que aparecieron
        const items = Array.from(document.querySelectorAll(
            '[role="menu"] *, [role="listbox"] *, [role="dialog"] *, [class*="menu"] *, [class*="popup"] *, [class*="dropdown"] *'
        )).filter(el => {
            if (!el.offsetParent) return false;
            const t = (el.innerText || '').trim();
            return t.length > 0 && t.length < 50;
        });
        // Si no hay menu, listar botones visibles
        if (items.length === 0) {
            return JSON.stringify(Array.from(document.querySelectorAll('button')).filter(b => {
                if (!b.offsetParent) return false;
                const text = (b.innerText || '').trim();
                return text.length > 0 && text.length < 50;
            }).map(b => (b.innerText || '').trim()).slice(0, 15));
        }
        return JSON.stringify(items.map(el => ({
            tag: el.tagName,
            text: (el.innerText || '').trim().substring(0, 40),
        })).slice(0, 15));
    })()""")
    log_info(f"[DESCARGA] Menu/opciones despues de Descargar: {menu_debug}")

    # Paso 3: Buscar opciones de descarga
    # Posibles: "Full Video"/"Video completo", "720p", "Clip", etc.
    log_info("[DESCARGA] Paso 3: Buscando opcion de descarga...")
    clicked_option = session.evaluate("""(() => {
        const normalize = (s) => String(s || '').toLowerCase().trim();

        // Buscar en items de menu/popup/dropdown
        const selectors = [
            '[role="menu"] button', '[role="menu"] [role="menuitem"]',
            '[role="listbox"] [role="option"]', '[role="dialog"] button',
            '[class*="menu"] button', '[class*="popup"] button',
            '[class*="dropdown"] button', '[class*="dropdown"] a',
        ].join(', ');

        let items = Array.from(document.querySelectorAll(selectors)).filter(el => el.offsetParent !== null);

        // Si no hay items de menu, buscar botones generales que aparecieron
        if (items.length === 0) {
            items = Array.from(document.querySelectorAll('button, a, [role="button"], [role="menuitem"]'))
                .filter(el => el.offsetParent !== null);
        }

        // Prioridad 1: "Full Video" / "Video completo" (exacto, no substring parcial)
        for (const item of items) {
            const text = normalize(item.innerText || item.textContent || '');
            // Solo matchear si es la opcion directa, no "ver panel completo"
            if ((text === 'full video' || text === 'video completo'
                || text.startsWith('full video') || text.startsWith('video completo'))
                && !text.includes('panel') && !text.includes('control')) {
                item.click();
                return 'CLICKED_FULL: ' + text.substring(0, 40);
            }
        }

        // Prioridad 2: Calidad directa (720p, 1080p)
        for (const item of items) {
            const text = normalize(item.innerText || item.textContent || '');
            if (text.includes('720') || text.includes('1080')) {
                item.click();
                return 'CLICKED_QUALITY: ' + text.substring(0, 40);
            }
        }

        // Prioridad 3: Cualquier opcion con "mp4" o "video" que no sea navegacion
        for (const item of items) {
            const text = normalize(item.innerText || item.textContent || '');
            if ((text.includes('mp4') || text.includes('.mp4'))
                && !text.includes('volver') && !text.includes('back')) {
                item.click();
                return 'CLICKED_FORMAT: ' + text.substring(0, 40);
            }
        }

        return 'NOT_FOUND';
    })()""")

    if clicked_option and "CLICKED" in str(clicked_option):
        log_ok(f"[DESCARGA] {clicked_option}")
        time.sleep(2)

        # Si fue Full Video, buscar submenu de calidad
        if "CLICKED_FULL" in str(clicked_option):
            log_info("[DESCARGA] Buscando calidad en submenu...")
            session.evaluate("""(() => {
                const items = Array.from(document.querySelectorAll(
                    'button, [role="menuitem"], [role="option"], a, span'
                )).filter(el => el.offsetParent !== null);
                for (const item of items) {
                    const text = (item.innerText || '').toLowerCase().trim();
                    if (text.includes('720') || text.includes('1080') || text.includes('mp4')) {
                        item.click();
                        return;
                    }
                }
            })()""")
            time.sleep(1)
    else:
        log_info(f"[DESCARGA] No se encontro menu de opciones ({clicked_option}). La descarga pudo haber iniciado directamente.")

    # Paso 4: Esperar archivo descargado
    log_info("[DESCARGA] Paso 4: Esperando archivo descargado...")
    deadline = time.time() + 120
    while time.time() < deadline:
        remaining = int(deadline - time.time())

        # Buscar cualquier archivo nuevo (mp4, webm, etc.)
        current_files = set(f.name for f in out_dir.iterdir() if f.is_file())
        new_files = current_files - existing_files
        # Filtrar archivos temporales
        new_files = {f for f in new_files if not f.endswith('.crdownload') and not f.startswith('.')}

        if new_files:
            for fname in new_files:
                fpath = out_dir / fname
                size = fpath.stat().st_size
                if size > 100_000:
                    time.sleep(3)
                    final_size = fpath.stat().st_size
                    if final_size == size:
                        size_mb = final_size / (1024 * 1024)
                        log_ok(f"[DESCARGA] Video descargado: {fname} ({size_mb:.1f}MB)")
                        return str(fpath)
                    log_info(f"[DESCARGA] Archivo creciendo... {size} → {final_size} bytes")

        downloading = list(out_dir.glob("*.crdownload"))
        if downloading and remaining % 10 < 3:
            log_info(f"[DESCARGA] Descarga en progreso... ({len(downloading)} archivo(s)) | quedan {remaining}s")

        time.sleep(2)

    log_error("[DESCARGA] Timeout (120s) esperando archivo descargado")
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
        file_path = _download_full_video(session, output_dir)
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
