"""
Test de conexión a DICloak Open API.

Uso:
  python -m core.dicloak_api.test_connection
  python -m core.dicloak_api.test_connection --port 52140 --key tu-api-key
  python -m core.dicloak_api.test_connection --open "nombre del perfil"
"""
from __future__ import annotations

import argparse
import sys
import os

# Agregar project root al path
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from core.dicloak_api.client import DICloakAPI


def main():
    parser = argparse.ArgumentParser(description="Test DICloak API connection")
    parser.add_argument("--port", type=int, default=0, help="Puerto de la API (default: DICLOAK_API_PORT o 52140)")
    parser.add_argument("--key", default="", help="API Key (default: DICLOAK_API_KEY)")
    parser.add_argument("--open", default="", help="Nombre del perfil a abrir (test de apertura)")
    parser.add_argument("--close", default="", help="ID del perfil a cerrar")
    args = parser.parse_args()

    # Cargar .env si existe
    env_path = Path(__file__).resolve().parent.parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

    api = DICloakAPI(port=args.port or 0, api_key=args.key or "")

    print(f"=== Test DICloak API ===")
    print(f"URL: {api.base_url}")
    print(f"API Key: {'***' + api.api_key[-4:] if len(api.api_key) > 4 else '(vacía)'}")
    print()

    # Test 1: Disponibilidad
    print("[1] Verificando conexión...")
    if api.is_available():
        print("    ✅ API disponible")
    else:
        print("    ❌ API no responde. Verifica que DICloak esté abierto y la Open API activada.")
        print(f"    URL intentada: {api.base_url}/api/v1/env/list")
        return 1

    # Test 2: Listar perfiles
    print("\n[2] Listando perfiles...")
    profiles = api.list_profiles()
    if profiles:
        print(f"    ✅ {len(profiles)} perfiles encontrados:")
        for p in profiles:
            print(f"       • {p.name} (ID: {p.id[:20]}..., status: {p.status})")
    else:
        print("    ⚠️  No se encontraron perfiles (puede ser que el endpoint sea diferente)")

    # Test 3: Perfiles running
    print("\n[3] Perfiles abiertos...")
    running = api.list_running()
    if running:
        print(f"    ✅ {len(running)} perfiles abiertos:")
        for p in running:
            print(f"       • {p.name} — CDP puerto: {p.debug_port}, PID: {p.pid}")
    else:
        print("    ℹ️  No hay perfiles abiertos")

    # Test 4: Abrir perfil (opcional)
    if args.open:
        print(f"\n[4] Abriendo perfil '{args.open}'...")
        try:
            result = api.open_profile_by_name(args.open)
            print(f"    ✅ Perfil abierto: {result.name}")
            print(f"       CDP puerto: {result.debug_port}")
            print(f"       WebSocket: {result.ws_url}")
        except Exception as e:
            print(f"    ❌ Error: {e}")

    # Test 5: Cerrar perfil (opcional)
    if args.close:
        print(f"\n[5] Cerrando perfil '{args.close}'...")
        ok = api.close_profile(args.close)
        print(f"    {'✅ Cerrado' if ok else '❌ No se pudo cerrar'}")

    print("\n=== Test completado ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
