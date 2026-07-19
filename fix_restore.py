"""Parchea cookies_dicloak.py: reconecta WS antes de restaurar localStorage."""
PATH = r"C:\Users\NyGsoft\Desktop\publicidad_david\server_dicloak\cookies_dicloak.py"

OLD = """            # Restaurar localStorage clave por clave
            if local_storage:
                ls_js = ";\n".join(
                    f"localStorage.setItem({json.dumps(k)}, {json.dumps(v)})"
                    for k, v in local_storage.items()
                )
                self._cdp_send(ws, 999, "Runtime.evaluate", {
                    "expression": ls_js,
                    "returnByValue": True,
                })
                time.sleep(0.5)"""

NEW = """            # Restaurar localStorage clave por clave
            # (reconectar porque setCookie puede haber cerrado el WS via reload)
            if local_storage:
                ls_js = ";\\n".join(
                    f"localStorage.setItem({json.dumps(k)}, {json.dumps(v)})"
                    for k, v in local_storage.items()
                )
                try:
                    self._cdp_send(ws, 999, "Runtime.evaluate", {
                        "expression": ls_js,
                        "returnByValue": True,
                    })
                    time.sleep(0.5)
                except Exception:
                    # WS cerrado tras reload — reconectar y reintentar
                    try:
                        ws.close()
                    except Exception:
                        pass
                    time.sleep(2)
                    new_page_id = self._get_page_id(port)
                    ws2 = self._cdp_connect(port, new_page_id or page_id)
                    try:
                        self._cdp_send(ws2, 999, "Runtime.evaluate", {
                            "expression": ls_js,
                            "returnByValue": True,
                        })
                        time.sleep(0.5)
                    except Exception:
                        pass
                    finally:
                        try:
                            ws2.close()
                        except Exception:
                            pass"""

src = open(PATH, encoding="utf-8").read()
if OLD in src:
    src = src.replace(OLD, NEW, 1)
    open(PATH, "w", encoding="utf-8").write(src)
    print("[OK] restore() parcheado para reconectar WS en localStorage")
elif "reconectar porque setCookie" in src:
    print("[OK] parche ya aplicado")
else:
    print("[ERROR] bloque no encontrado")
