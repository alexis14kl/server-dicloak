import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cdp_bridge import open_profile_via_cdp, _test_cdp_port
from platform_utils import get_process_list
import re

name = sys.argv[1] if len(sys.argv) > 1 else "#1 Chat Gpt PRO"
print(f"Abriendo perfil: {name}")

result = open_profile_via_cdp(name)
print(f"Resultado: {result} (tipo: {type(result).__name__})")

# Buscar puertos ginsbrowser activos
print("\nPuertos ginsbrowser activos:")
for p in get_process_list():
    cmdline = p.get("cmdline", "")
    if "ginsbrowser" in p.get("name", "").lower() and "--remote-debugging-port=" in cmdline:
        m = re.search(r"--remote-debugging-port=(\d+)", cmdline)
        if m:
            port = int(m.group(1))
            active = _test_cdp_port(port)
            print(f"  Puerto {port} - CDP activo: {active}")
