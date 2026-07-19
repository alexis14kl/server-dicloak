"""
Parchea server.py para auto-llamar inject_shortcuts() despues de open_profile.
Usa ast para localizar la funcion con precision.
"""
import re, sys

SERVER_PATH = r"C:\Users\NyGsoft\Desktop\publicidad_david\server_dicloak\server.py"

with open(SERVER_PATH, "r", encoding="utf-8") as f:
    src = f.read()

# ---------- 1. Agregar import threading si no existe ----------
if "import threading" not in src:
    # insertar despues de la ultima linea de imports (busca primer blanco post-imports)
    src = re.sub(
        r"(^import os\b)",
        r"import threading\n\1",
        src,
        count=1,
        flags=re.MULTILINE
    )
    print("[OK] 'import threading' agregado")
else:
    print("[OK] 'import threading' ya existe")

# ---------- 2. Agregar import inject_shortcuts ----------
if "inject_shortcuts" not in src:
    src = re.sub(
        r"(^import threading\b)",
        r"\1\nfrom cookies_dicloak import inject_shortcuts",
        src,
        count=1,
        flags=re.MULTILINE
    )
    print("[OK] 'from cookies_dicloak import inject_shortcuts' agregado")
else:
    print("[OK] inject_shortcuts ya importado")

# ---------- 3. Parchar open_profile para llamar inject_shortcuts ----------
PATCH_MARKER = "# __AUTO_INJECT_SHORTCUTS__"

if PATCH_MARKER in src:
    print("[OK] Parche ya aplicado anteriormente, omitiendo")
else:
    OLD = 'return success_response(data={"profile": profile}, message=f"Perfil \'{req.name}\' abierto")'
    NEW = f"""# {PATCH_MARKER}
           _port = int(profile.get("debug_port") or 0)
           if _port and profile.get("cdp_active"):
               threading.Thread(
                   target=inject_shortcuts, args=(_port,), daemon=True
               ).start()
           return success_response(data={{"profile": profile}}, message=f"Perfil '{{req.name}}' abierto")"""

    if OLD in src:
        src = src.replace(OLD, NEW, 1)
        print("[OK] open_profile parcheado con inject_shortcuts auto-call")
    else:
        print("[ERROR] No se encontro la linea exacta en open_profile - buscando variante...")
        # intento alternativo con patron mas flexible
        pattern = r'(return success_response\(data=\{"profile": profile\}, message=f"Perfil \')'
        if re.search(pattern, src):
            src = re.sub(
                r'(return success_response\(data=\{"profile": profile\}, message=f"Perfil \'[^"]+\'[^)]+\))',
                NEW,
                src,
                count=1
            )
            print("[OK] open_profile parcheado (patron alternativo)")
        else:
            print("[FAIL] No se pudo parchar open_profile")
            sys.exit(1)

with open(SERVER_PATH, "w", encoding="utf-8") as f:
    f.write(src)

print("[DONE] server.py guardado")
