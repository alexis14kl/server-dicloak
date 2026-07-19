"""Extrae cookies de TEST_COOKIE1 y las guarda en cache directamente"""
import sys, json
sys.path.insert(0, r"C:\Users\NyGsoft\Desktop\publicidad_david\server_dicloak")

from cookies_dicloak import CookieManager
import urllib.request

# Obtener el puerto CDP del perfil abierto
try:
    profiles_raw = urllib.request.urlopen("http://127.0.0.1:8585/profiles/running", timeout=5).read()
    profiles = json.loads(profiles_raw)
    running = profiles.get("data", {}).get("profiles", [])
    port = 0
    for p in running:
        if "TEST_COOKIE1" in p.get("name", "") and p.get("cdp_active"):
            port = int(p.get("debug_port", 0))
            break
    print(f"Puerto CDP encontrado via API: {port}")
except Exception as e:
    print(f"API no disponible: {e}")
    port = 0

if not port:
    # Intentar con el puerto conocido de la sesion anterior
    port = 53706
    print(f"Usando puerto anterior: {port}")

# Extraer y guardar
mgr = CookieManager()
try:
    info = mgr.extract_and_save(port=port, profile_name="TEST_COOKIE1")
    print(f"[OK] Cookies guardadas para: {info.profile_name}")
    print(f"     Session valida: {info.is_session_valid}")
    print(f"     Dias hasta expirar: {round(info.session_expires_in_days, 1)}")
    print(f"     CF Clearance dias: {round(info.cf_expires_in_days, 1)}")
    print(f"     Necesita rewarm: {info.needs_rewarm}")
    d = info.to_dict()
    print(f"     Cookies totales: {d.get('cookies_count', '?')}")
except Exception as e:
    print(f"[ERROR] {e}")
    import traceback; traceback.print_exc()
