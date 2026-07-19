"""Integra build_router de cookies_dicloak en server.py y hace extract+restore de TEST_COOKIE1"""
import sys, re

SERVER = r"C:\Users\NyGsoft\Desktop\publicidad_david\server_dicloak\server.py"

with open(SERVER, encoding="utf-8") as f:
    src = f.read()

# 1. Agregar import build_router si no existe
if "build_router" not in src:
    src = src.replace(
        "from cookies_dicloak import inject_shortcuts",
        "from cookies_dicloak import inject_shortcuts, build_router"
    )
    print("[OK] build_router agregado al import")
else:
    print("[OK] build_router ya importado")

# 2. Incluir el router en la app (buscar donde se incluyen otros routers o al final de imports)
ROUTER_CALL = "\n# Cookies router\napp.include_router(build_router(), prefix=\"\")\n"
if "build_router()" not in src:
    # insertar antes del primer @app.get o @app.post
    idx = src.find("\n@app.")
    if idx >= 0:
        src = src[:idx] + ROUTER_CALL + src[idx:]
        print("[OK] app.include_router(build_router()) insertado")
    else:
        src += ROUTER_CALL
        print("[OK] app.include_router agregado al final")
else:
    print("[OK] router ya registrado")

with open(SERVER, "w", encoding="utf-8") as f:
    f.write(src)

print("[DONE] server.py actualizado")
