"""
1. Parchea server.py para guardar port→nombre en cache/port_map.json al abrir perfil
2. Agrega POST /cookies/extract?port=X que detecta el nombre automaticamente
"""
import re

SERVER = r"C:\Users\NyGsoft\Desktop\publicidad_david\server_dicloak\server.py"
COOKIES = r"C:\Users\NyGsoft\Desktop\publicidad_david\server_dicloak\cookies_dicloak.py"

# ── 1. Parchar server.py ──────────────────────────────────────────────────────
src = open(SERVER, encoding="utf-8").read()

PORT_MAP_SAVE = """        # Guardar mapeo port→nombre para smart_extract
        _dbg = int(profile.get("debug_port") or 0)
        if _dbg:
            import json as _j, pathlib as _pl
            _pm = _pl.Path(__file__).parent / "cache" / "port_map.json"
            _pm.parent.mkdir(exist_ok=True)
            _data = _j.loads(_pm.read_text()) if _pm.exists() else {}
            _data[str(_dbg)] = profile.get("name", req.name)
            _pm.write_text(_j.dumps(_data, indent=2))
"""

MARKER = "        # __AUTO_INJECT_SHORTCUTS__"
if MARKER in src and "port_map.json" not in src:
    src = src.replace(MARKER, PORT_MAP_SAVE + "        # __AUTO_INJECT_SHORTCUTS__")
    open(SERVER, "w", encoding="utf-8").write(src)
    print("[OK] server.py: guardado port_map.json en open_profile")
elif "port_map.json" in src:
    print("[OK] server.py: port_map.json ya integrado")
else:
    print("[WARN] server.py: marker no encontrado, saltando")

# ── 2. Agregar POST /cookies/extract en cookies_dicloak.py ───────────────────
lines = open(COOKIES, encoding="utf-8").read().splitlines()

# Buscar punto de insercion (antes del @router.get("/"))
insert_at = None
for i, line in enumerate(lines):
    if '    @router.get("/")' in line:
        insert_at = i
        break

if insert_at is None:
    print("[ERROR] cookies_dicloak.py: punto de insercion no encontrado")
    raise SystemExit(1)

# Verificar que no existe ya
already = any('/cookies/extract' in l or 'smart_extract' in l for l in lines)
if already:
    print("[OK] cookies_dicloak.py: smart_extract ya existe")
else:
    NEW_BLOCK = [
        '    @router.post("/extract")',
        '    def smart_extract(port: int):',
        '        """',
        '        Extrae cookies del perfil abierto en `port`.',
        '        El nombre se detecta automaticamente — no hace falta en la URL.',
        '        Uso: POST /cookies/extract?port=54937',
        '        """',
        '        import urllib.request as _ur, pathlib as _pl',
        '',
        '        # Buscar nombre en port_map.json',
        '        profile_name = ""',
        '        try:',
        '            pm_path = _pl.Path(__file__).parent / "cache" / "port_map.json"',
        '            if pm_path.exists():',
        '                pm = _json.loads(pm_path.read_text())',
        '                profile_name = pm.get(str(port), "")',
        '        except Exception:',
        '            pass',
        '',
        '        # Fallback: consultar /profiles/running (por si acaso)',
        '        if not profile_name:',
        '            try:',
        '                raw = _ur.urlopen("http://127.0.0.1:8585/profiles/running", timeout=5).read()',
        '                data = _json.loads(raw)',
        '                for p in data.get("data", {}).get("profiles", []):',
        '                    if int(p.get("debug_port", 0)) == port:',
        '                        profile_name = p.get("name", "")',
        '                        break',
        '            except Exception:',
        '                pass',
        '',
        '        if not profile_name:',
        '            return err(f"Puerto {port} no encontrado — abrir perfil primero", 404)',
        '',
        '        try:',
        '            info = mgr.extract_and_save(port=port, profile_name=profile_name)',
        '            d = info.to_dict()',
        '            return ok(data=d, msg=f"Cache guardado — perfil: {profile_name}, {d.get(\"cookies_count\",\"?\")} cookies")',
        '        except Exception as e:',
        '            return err(str(e), 500)',
        '',
    ]
    new_lines = lines[:insert_at] + NEW_BLOCK + lines[insert_at:]
    open(COOKIES, "w", encoding="utf-8").write("\n".join(new_lines))
    print(f"[OK] cookies_dicloak.py: POST /cookies/extract agregado ({len(NEW_BLOCK)} lineas)")

# ── 3. Verificar sintaxis ─────────────────────────────────────────────────────
import subprocess, sys
r1 = subprocess.run([sys.executable, "-c", f"import py_compile; py_compile.compile(r'{SERVER}', doraise=True)"], capture_output=True)
r2 = subprocess.run([sys.executable, "-c", f"import py_compile; py_compile.compile(r'{COOKIES}', doraise=True)"], capture_output=True)
print("[OK] server.py sintaxis OK" if r1.returncode == 0 else f"[ERROR] server.py: {r1.stderr.decode()}")
print("[OK] cookies_dicloak.py sintaxis OK" if r2.returncode == 0 else f"[ERROR] cookies_dicloak.py: {r2.stderr.decode()}")
