"""Corrige la indentacion del bloque inject_shortcuts en server.py"""
SERVER = r"C:\Users\NyGsoft\Desktop\publicidad_david\server_dicloak\server.py"

with open(SERVER, encoding="utf-8") as f:
    src = f.read()

OLD = """        profile = service.open_profile(req.name, req.timeout)
        # # __AUTO_INJECT_SHORTCUTS__
           _port = int(profile.get("debug_port") or 0)
           if _port and profile.get("cdp_active"):
               threading.Thread(
                   target=inject_shortcuts, args=(_port,), daemon=True
               ).start()
           return success_response(data={"profile": profile}, message=f"Perfil '{req.name}' abierto")"""

NEW = """        profile = service.open_profile(req.name, req.timeout)
        # __AUTO_INJECT_SHORTCUTS__
        _port = int(profile.get("debug_port") or 0)
        if _port and profile.get("cdp_active"):
            threading.Thread(
                target=inject_shortcuts, args=(_port,), daemon=True
            ).start()
        return success_response(data={"profile": profile}, message=f"Perfil '{req.name}' abierto")"""

if OLD in src:
    src = src.replace(OLD, NEW, 1)
    with open(SERVER, "w", encoding="utf-8") as f:
        f.write(src)
    print("[OK] Indentacion corregida")
else:
    print("[ERROR] Bloque no encontrado, dump del area:")
    idx = src.find("__AUTO_INJECT_SHORTCUTS__")
    if idx >= 0:
        print(repr(src[max(0,idx-200):idx+300]))
    else:
        print("MARKER tampoco encontrado")
