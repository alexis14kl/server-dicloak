path = r"C:\Users\NyGsoft\Desktop\publicidad_david\server_dicloak\server.py"
src = open(path, encoding="utf-8").read()

OLD = 'app.include_router(build_router(), prefix="")'
NEW = 'app.include_router(build_router(), prefix="/cookies")'

if OLD in src:
    src = src.replace(OLD, NEW, 1)
    open(path, "w", encoding="utf-8").write(src)
    print("OK - prefix corregido a /cookies")
elif '/cookies' in src and 'include_router' in src:
    print("OK - ya tiene /cookies")
else:
    print("ERROR - no encontrado:", repr(src[src.find('include_router')-5:src.find('include_router')+60]))
