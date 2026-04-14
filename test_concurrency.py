"""
Pruebas de concurrencia del routing por pool — sin DICloak real.

Que prueba:
    1. /pools devuelve la estructura esperada
    2. resolve_pool_port aplica las reglas de routing correctamente
    3. Un /chatgpt/* y un /veo3/* concurrentes ejecutan EN PARALELO
       (locks distintos) — verificado midiendo tiempos.
    4. Dos /chatgpt/* concurrentes se SERIALIZAN (mismo lock).
    5. Dos /veo3/* concurrentes se SERIALIZAN (mismo lock).
    6. El shutdown event interrumpe interruptible_sleep al instante.

Como funciona:
    Las funciones de trabajo reales (paste_and_send_prompt, extend_video,
    wait_and_download_image) hacen CDP y dependen de un browser real, asi
    que las monkey-patcheamos por funciones que solo duermen y registran
    timestamps. Tambien stub'amos get_running_profiles, _test_cdp_port y
    classify_port para simular dos perfiles activos (uno image, uno video)
    sin necesidad de DICloak corriendo.

    Las pruebas usan fastapi.testclient.TestClient + threads. TestClient
    corre la app sin un servidor HTTP real — los handlers ejecutan en el
    mismo proceso, en el threadpool de Starlette tal como en produccion.
"""
from __future__ import annotations

import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# Asegurar que importa desde el directorio del proyecto
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))


# ─── Stubs que simulan dos perfiles activos ──────────────────────────────────

FAKE_IMAGE_PORT = 19050  # perfil ChatGPT simulado
FAKE_VIDEO_PORT = 19051  # perfil Veo3 simulado

_fake_running = [
    {"pid": 1001, "debug_port": FAKE_IMAGE_PORT, "cdp_active": True},
    {"pid": 1002, "debug_port": FAKE_VIDEO_PORT, "cdp_active": True},
]


def _stub_get_running_profiles(self=None):
    return _fake_running


def _stub_test_cdp_port(port):
    return port in (FAKE_IMAGE_PORT, FAKE_VIDEO_PORT)


def _stub_classify_port(port):
    if port == FAKE_IMAGE_PORT:
        return "image"
    if port == FAKE_VIDEO_PORT:
        return "video"
    return None


# Eventos para registrar timestamps de cada llamada al worker
_call_log: list[tuple[str, str, float]] = []  # (kind, event, ts)
_call_log_lock = threading.Lock()


def _log_event(kind: str, event: str) -> None:
    with _call_log_lock:
        _call_log.append((kind, event, time.perf_counter()))


def _stub_chatgpt_work(duration: float, kind: str = "chatgpt"):
    """Worker stub que simula trabajo del flujo ChatGPT."""
    _log_event(kind, "start")
    time.sleep(duration)
    _log_event(kind, "end")
    return {"success": True, "stub": True}


def _stub_veo3_work(duration: float, kind: str = "veo3"):
    """Worker stub que simula trabajo del flujo Veo3."""
    _log_event(kind, "start")
    time.sleep(duration)
    _log_event(kind, "end")
    return {"success": True, "stub": True}


# ─── Instalacion de monkey-patches antes de importar server ──────────────────

import profile_pool  # noqa: E402

# Reemplazar la clasificacion por nuestro stub determinista
profile_pool.classify_port = _stub_classify_port  # type: ignore
# Tambien parchar _test_cdp_port que profile_pool importo
profile_pool._test_cdp_port = _stub_test_cdp_port  # type: ignore

import cdp_bridge  # noqa: E402
cdp_bridge._test_cdp_port = _stub_test_cdp_port  # type: ignore

import server  # noqa: E402

# Stub del DICloakService.get_running_profiles
server.service.get_running_profiles = lambda: _fake_running  # type: ignore
# Tambien hay que parchar el _test_cdp_port que server importo a su namespace
server._test_cdp_port = _stub_test_cdp_port  # type: ignore


# Monkey-patch del worker chatgpt (la funcion interna que el endpoint llama
# DESPUES de adquirir el lock). Nos saltamos toda la maquinaria CDP real.
def _fake_chatgpt_prompt_locked(req, port):
    _stub_chatgpt_work(0.6, kind=f"chatgpt:{port}")
    return server.success_response(data={"port": port, "stub": True}, message="stub")


server._chatgpt_prompt_locked = _fake_chatgpt_prompt_locked  # type: ignore


# Para /veo3/extend-video reemplazamos el modulo importado dinamicamente.
# El endpoint hace `from chat_veo3_videos.exten_video import extend_video`
# dentro del handler, asi que tenemos que registrar un fake en sys.modules.
import types  # noqa: E402

_fake_exten = types.ModuleType("chat_veo3_videos.exten_video")
_fake_exten.extend_video = lambda port, prompt: _stub_veo3_work(0.6, kind=f"veo3:{port}")  # type: ignore
_fake_exten.download_extended_video = lambda port, timeout, output_dir: _stub_veo3_work(0.4, kind=f"veo3-dl:{port}")  # type: ignore
sys.modules["chat_veo3_videos.exten_video"] = _fake_exten

_fake_veo3_session = types.ModuleType("chat_veo3_videos.veo3_session")
_fake_veo3_session.navigate_and_stabilize = lambda port, timeout: _stub_veo3_work(0.2, kind=f"veo3-stab:{port}")  # type: ignore
_fake_veo3_session.open_new_project = lambda port, prompt: _stub_veo3_work(0.2, kind=f"veo3-new:{port}")  # type: ignore
sys.modules["chat_veo3_videos.veo3_session"] = _fake_veo3_session


from fastapi.testclient import TestClient  # noqa: E402

client = TestClient(server.app)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def reset_log():
    with _call_log_lock:
        _call_log.clear()


def _format_timeline() -> str:
    if not _call_log:
        return "  (vacio)"
    t0 = _call_log[0][2]
    lines = [f"  +{(ts - t0)*1000:7.1f}ms  {kind:25s}  {event}" for kind, event, ts in _call_log]
    return "\n".join(lines)


def _spans():
    """Devuelve {kind: (start_ts, end_ts)} a partir del log."""
    starts: dict[str, float] = {}
    spans: dict[str, tuple[float, float]] = {}
    for kind, event, ts in _call_log:
        if event == "start":
            starts[kind] = ts
        elif event == "end" and kind in starts:
            spans[kind] = (starts[kind], ts)
    return spans


def _spans_overlap(a: tuple[float, float], b: tuple[float, float]) -> bool:
    return a[0] < b[1] and b[0] < a[1]


# ─── Tests ────────────────────────────────────────────────────────────────────

def test_pools_endpoint():
    print("\n[TEST 1] GET /pools devuelve estructura con ambos pools")
    r = client.get("/pools")
    assert r.status_code == 200, f"esperado 200, recibido {r.status_code}: {r.text}"
    data = r.json()["data"]
    pools = data["pools"]
    assert "image" in pools and "video" in pools
    assert FAKE_IMAGE_PORT in pools["image"]["ports"], f"image ports: {pools['image']['ports']}"
    assert FAKE_VIDEO_PORT in pools["video"]["ports"], f"video ports: {pools['video']['ports']}"
    assert pools["image"]["lock_busy"] is False
    assert pools["video"]["lock_busy"] is False
    print(f"  pools.image.ports = {pools['image']['ports']}")
    print(f"  pools.video.ports = {pools['video']['ports']}")
    print(f"  total_running = {data['total_running']}")
    print("  PASS")


def test_resolve_routing_correct_pool():
    print("\n[TEST 2] resolve_pool_port: puerto correcto para su pool")
    p, err = server.resolve_pool_port(FAKE_IMAGE_PORT, "image")
    assert err == "" and p == FAKE_IMAGE_PORT, f"got ({p}, {err!r})"
    p, err = server.resolve_pool_port(FAKE_VIDEO_PORT, "video")
    assert err == "" and p == FAKE_VIDEO_PORT, f"got ({p}, {err!r})"
    print("  PASS")


def test_resolve_routing_wrong_pool_redirects():
    print("\n[TEST 3] resolve_pool_port: puerto del pool equivocado se redirige")
    # Cliente manda puerto video a endpoint que pide image
    p, err = server.resolve_pool_port(FAKE_VIDEO_PORT, "image")
    assert err == "" and p == FAKE_IMAGE_PORT, f"esperado redirect a {FAKE_IMAGE_PORT}, got ({p}, {err!r})"
    # Y al reves
    p, err = server.resolve_pool_port(FAKE_IMAGE_PORT, "video")
    assert err == "" and p == FAKE_VIDEO_PORT, f"esperado redirect a {FAKE_VIDEO_PORT}, got ({p}, {err!r})"
    print("  PASS — image->video y viceversa redirige correctamente")


def test_resolve_routing_dead_port():
    print("\n[TEST 4] resolve_pool_port: puerto muerto se reemplaza dinamicamente")
    p, err = server.resolve_pool_port(99999, "image")
    assert err == "" and p == FAKE_IMAGE_PORT, f"got ({p}, {err!r})"
    p, err = server.resolve_pool_port(0, "video")
    assert err == "" and p == FAKE_VIDEO_PORT, f"got ({p}, {err!r})"
    print("  PASS")


def test_concurrent_image_and_video_run_in_parallel():
    print("\n[TEST 5] /chatgpt/prompt + /veo3/extend-video CONCURRENTES -> en paralelo")
    reset_log()

    def fire_image():
        return client.post("/chatgpt/prompt", json={
            "port": FAKE_IMAGE_PORT, "prompt": "test", "auto_rotate": False,
        })

    def fire_video():
        return client.post("/veo3/extend-video", json={
            "port": FAKE_VIDEO_PORT, "prompt": "test",
        })

    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=2) as ex:
        f1 = ex.submit(fire_image)
        f2 = ex.submit(fire_video)
        r1 = f1.result(timeout=10)
        r2 = f2.result(timeout=10)
    elapsed = time.perf_counter() - t0

    assert r1.status_code == 200, f"image: {r1.status_code} {r1.text}"
    assert r2.status_code == 200, f"video: {r2.status_code} {r2.text}"

    spans = _spans()
    img_kind = f"chatgpt:{FAKE_IMAGE_PORT}"
    vid_kind = f"veo3:{FAKE_VIDEO_PORT}"
    assert img_kind in spans, f"no se ejecuto worker image: {list(spans.keys())}"
    assert vid_kind in spans, f"no se ejecuto worker video: {list(spans.keys())}"

    overlap = _spans_overlap(spans[img_kind], spans[vid_kind])
    print("  Timeline:")
    print(_format_timeline())
    print(f"  Tiempo total wall-clock: {elapsed*1000:.0f}ms")
    print(f"  Overlap image/video: {overlap}")
    assert overlap, "image y video DEBERIAN ejecutarse en paralelo (locks distintos)"
    # Cada worker dura 0.6s. Si fueran serializados serian ~1.2s. Damos margen
    # generoso para CI (0.95s) sin perder la senal.
    assert elapsed < 0.95, f"tiempo total {elapsed:.2f}s sugiere serializacion (esperado <0.95s)"
    print("  PASS — corren en paralelo")


def test_concurrent_two_image_serialize():
    print("\n[TEST 6] dos /chatgpt/prompt CONCURRENTES -> se serializan (mismo lock)")
    reset_log()

    def fire():
        return client.post("/chatgpt/prompt", json={
            "port": FAKE_IMAGE_PORT, "prompt": "test", "auto_rotate": False,
        })

    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=2) as ex:
        f1 = ex.submit(fire)
        f2 = ex.submit(fire)
        r1 = f1.result(timeout=10)
        r2 = f2.result(timeout=10)
    elapsed = time.perf_counter() - t0

    assert r1.status_code == 200 and r2.status_code == 200

    # Ambos workers usan el mismo kind, asi que en _spans solo queda el ultimo.
    # Verificamos por el log directo que hubo 2 starts y 2 ends.
    starts = sum(1 for _, e, _ in _call_log if e == "start")
    ends = sum(1 for _, e, _ in _call_log if e == "end")
    assert starts == 2 and ends == 2, f"esperado 2/2, got {starts}/{ends}"

    # Para serializacion: el segundo start debe ser DESPUES del primer end.
    events = [(k, e, ts) for k, e, ts in _call_log]
    first_end_ts = next(ts for _, e, ts in events if e == "end")
    second_start_ts = sorted(ts for _, e, ts in events if e == "start")[1]
    print("  Timeline:")
    print(_format_timeline())
    print(f"  Tiempo total wall-clock: {elapsed*1000:.0f}ms")
    print(f"  Segundo start vs primer end: {(second_start_ts - first_end_ts)*1000:+.1f}ms")
    assert second_start_ts >= first_end_ts - 0.005, "el segundo request entro antes de que terminara el primero — el lock no funciono"
    assert elapsed > 1.0, f"tiempo total {elapsed:.2f}s — esperado >1.0s (serializacion de 2 x 0.6s)"
    print("  PASS — serializados correctamente")


def test_concurrent_two_video_serialize():
    print("\n[TEST 7] dos /veo3/extend-video CONCURRENTES -> se serializan (mismo lock)")
    reset_log()

    def fire():
        return client.post("/veo3/extend-video", json={
            "port": FAKE_VIDEO_PORT, "prompt": "test",
        })

    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=2) as ex:
        f1 = ex.submit(fire)
        f2 = ex.submit(fire)
        r1 = f1.result(timeout=10)
        r2 = f2.result(timeout=10)
    elapsed = time.perf_counter() - t0

    assert r1.status_code == 200 and r2.status_code == 200
    starts = sum(1 for _, e, _ in _call_log if e == "start")
    ends = sum(1 for _, e, _ in _call_log if e == "end")
    assert starts == 2 and ends == 2
    print(f"  Tiempo total wall-clock: {elapsed*1000:.0f}ms")
    assert elapsed > 1.0, f"tiempo total {elapsed:.2f}s — esperado >1.0s"
    print("  PASS — serializados correctamente")


def test_mixed_burst_three_image_one_video():
    print("\n[TEST 8] burst mixto: 3 image + 1 video concurrentes")
    reset_log()

    def img():
        return client.post("/chatgpt/prompt", json={"port": FAKE_IMAGE_PORT, "prompt": "p", "auto_rotate": False})

    def vid():
        return client.post("/veo3/extend-video", json={"port": FAKE_VIDEO_PORT, "prompt": "p"})

    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=4) as ex:
        futures = [ex.submit(img), ex.submit(img), ex.submit(img), ex.submit(vid)]
        results = [f.result(timeout=15) for f in futures]
    elapsed = time.perf_counter() - t0

    for r in results:
        assert r.status_code == 200, f"{r.status_code}: {r.text}"

    # Tres image deberian serializarse (~1.8s) y el unico video correr en
    # paralelo (~0.6s). Wall-clock dominado por los 3 image: ~1.8s.
    print(f"  Tiempo total wall-clock: {elapsed*1000:.0f}ms")
    print(f"  Eventos en log: {len(_call_log)}")
    assert 1.5 < elapsed < 2.5, f"esperado ~1.8s (3*0.6s), got {elapsed:.2f}s"
    # El video debe haber corrido a la par del primer image, no esperarlos.
    spans = _spans()
    vid_span = spans.get(f"veo3:{FAKE_VIDEO_PORT}")
    assert vid_span is not None, "no se ejecuto el worker video"
    vid_duration = vid_span[1] - vid_span[0]
    print(f"  Video duro {vid_duration*1000:.0f}ms (esperado ~600ms)")
    assert vid_duration < 0.95, "video se demoro mas de lo esperado — habria sido serializado"
    print("  PASS — los image se serializan, el video corre en paralelo")


def test_pools_endpoint_shows_lock_busy_during_request():
    print("\n[TEST 9] /pools muestra lock_busy=True durante un request en vuelo")
    reset_log()

    busy_during_request: dict[str, bool] = {}

    def slow_request():
        client.post("/chatgpt/prompt", json={"port": FAKE_IMAGE_PORT, "prompt": "p", "auto_rotate": False})

    with ThreadPoolExecutor(max_workers=2) as ex:
        future = ex.submit(slow_request)
        time.sleep(0.15)  # dejar que el request entre y tome el lock
        r = client.get("/pools")
        data = r.json()["data"]
        busy_during_request["image"] = data["pools"]["image"]["lock_busy"]
        busy_during_request["video"] = data["pools"]["video"]["lock_busy"]
        future.result(timeout=10)

    print(f"  Durante request: image lock_busy={busy_during_request['image']}, video={busy_during_request['video']}")
    assert busy_during_request["image"] is True, "image lock deberia estar tomado durante el request"
    assert busy_during_request["video"] is False, "video lock no deberia estar tomado"

    # Despues del request, ambos libres
    r = client.get("/pools")
    after = r.json()["data"]["pools"]
    assert after["image"]["lock_busy"] is False
    print("  PASS — /pools refleja correctamente el estado del lock en vivo")


def test_shutdown_event_interrupts_sleep():
    print("\n[TEST 10] shutdown event interrumpe interruptible_sleep al instante")
    from cancellation import (
        interruptible_sleep, request_shutdown, JobCancelled, _shutdown_event,
    )

    # Reset por si otro test lo dejo seteado
    _shutdown_event.clear()

    elapsed_holder = {}
    error_holder = {}

    def sleeper():
        t0 = time.perf_counter()
        try:
            interruptible_sleep(10.0)
        except JobCancelled as e:
            error_holder["err"] = str(e)
        elapsed_holder["t"] = time.perf_counter() - t0

    th = threading.Thread(target=sleeper, daemon=True)
    th.start()
    time.sleep(0.2)
    request_shutdown()
    th.join(timeout=3)

    print(f"  Sleep planeado: 10s, real: {elapsed_holder['t']*1000:.0f}ms")
    print(f"  Excepcion: {error_holder.get('err')}")
    assert "shutting down" in error_holder.get("err", "")
    assert elapsed_holder["t"] < 0.5, f"sleep no se interrumpio rapido: {elapsed_holder['t']:.2f}s"

    # Restaurar para que los siguientes tests no vean el flag puesto
    _shutdown_event.clear()
    print("  PASS — shutdown event despierta interruptible_sleep en <500ms")


# ─── Runner ───────────────────────────────────────────────────────────────────

def main():
    tests = [
        test_pools_endpoint,
        test_resolve_routing_correct_pool,
        test_resolve_routing_wrong_pool_redirects,
        test_resolve_routing_dead_port,
        test_concurrent_image_and_video_run_in_parallel,
        test_concurrent_two_image_serialize,
        test_concurrent_two_video_serialize,
        test_mixed_burst_three_image_one_video,
        test_pools_endpoint_shows_lock_busy_during_request,
        test_shutdown_event_interrupts_sleep,
    ]
    print(f"{'='*70}")
    print(f" Pruebas de concurrencia del routing por pool")
    print(f"{'='*70}")
    failed = []
    for t in tests:
        try:
            t()
        except AssertionError as e:
            print(f"  FAIL: {e}")
            failed.append((t.__name__, str(e)))
        except Exception as e:
            import traceback
            print(f"  ERROR: {type(e).__name__}: {e}")
            traceback.print_exc()
            failed.append((t.__name__, f"{type(e).__name__}: {e}"))

    print(f"\n{'='*70}")
    if not failed:
        print(f" RESULTADO: {len(tests)} tests, todos PASS")
        print(f"{'='*70}")
        return 0
    print(f" RESULTADO: {len(tests) - len(failed)}/{len(tests)} PASS")
    for name, err in failed:
        print(f"   - {name}: {err}")
    print(f"{'='*70}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
