# API de Cache de Cookies (DICloak RPA)

Módulo `cookies_dicloak.py` — extrae, guarda y restaura sesiones de ChatGPT
entre perfiles de DICloak vía CDP. Permite reutilizar una sesión activa en
cualquier perfil sin necesidad de loguearse de nuevo.

---

## Arquitectura

```
open_profile
    │
    ├── extract_and_save()  →  cache/cookies/{Perfil}.json
    │       55 cookies + 34 localStorage keys
    │
    └── restore()  ←  cache/cookies/{Perfil_Fuente}.json
            inyecta sesión de otro perfil en el navegador abierto
```

### Archivos generados

| Ruta | Descripción |
|---|---|
| `cache/cookies/{Nombre_Perfil}.json` | Cache de cookies + localStorage + metadatos de sesión |

El nombre del archivo reemplaza espacios por `_`. Ej: `#1 Chat Gpt Pro` → `#1_Chat_Gpt_Pro.json`.

---

## Endpoints del API (server.py puerto 8585)

> El router se registra en `server.py` con `app.include_router(build_router(), prefix="/cookies")`.

### GET `/cookies/`
Lista todos los perfiles con cache guardado y estado de sesión.

```bash
curl http://127.0.0.1:8585/cookies/
```

Respuesta:
```json
{
  "success": true,
  "message": "OK",
  "data": [
    {
      "profile_name": "#1 Chat Gpt Pro",
      "session_valid": true,
      "session_expires_in_days": 88.3,
      "cf_clearance_expires_in_days": 363.1,
      "needs_rewarm": false,
      "cookies_count": 55
    }
  ]
}
```

---

### GET `/cookies/{profile_name}/info`
Estado de sesión de un perfil sin abrir el navegador.

```bash
curl http://127.0.0.1:8585/cookies/%231%20Chat%20Gpt%20Pro/info
```

Respuesta:
```json
{
  "success": true,
  "data": {
    "profile_name": "#1 Chat Gpt Pro",
    "session_valid": true,
    "session_expires_in_days": 88.3,
    "cf_clearance_expires_in_days": 363.1,
    "needs_rewarm": false
  }
}
```

> `needs_rewarm: true` → sesión expira en menos de 7 días, conviene renovarla.

---

### POST `/cookies/{profile_name}/extract?port={cdp_port}`
Extrae las cookies del perfil **abierto** y las guarda en cache.

```bash
# 1. Obtener el puerto CDP del perfil abierto
curl http://127.0.0.1:8585/profiles/running

# 2. Extraer y guardar
curl -X POST "http://127.0.0.1:8585/cookies/TEST_COOKIE1/extract?port=53706"
```

Respuesta:
```json
{
  "success": true,
  "message": "OK",
  "data": {
    "profile_name": "TEST_COOKIE1",
    "cookies_saved": 55,
    "localStorage_keys": 34,
    "session_valid": true,
    "session_expires_in_days": 88.3
  }
}
```

---

### POST `/cookies/{profile_name}/restore?port={cdp_port}`
Restaura las cookies guardadas en el perfil **abierto** (mismo perfil).

```bash
curl -X POST "http://127.0.0.1:8585/cookies/%231%20Chat%20Gpt%20Pro/restore?port=53706"
```

Respuesta:
```json
{
  "success": true,
  "data": {
    "restored_cookies": 51,
    "failed_cookies": 0,
    "skipped_expired": 4,
    "localStorage_keys_restored": 34
  }
}
```

---

## Inyección entre perfiles (cross-profile)

Permite usar la sesión de un perfil logueado en un perfil nuevo sin logueo manual.
Usar el script `inject_session.py`:

```python
SOURCE_PROFILE = "#1 Chat Gpt Pro"   # perfil con sesión guardada en cache
TARGET_PORT    = 53706               # puerto CDP del perfil destino abierto
```

```bash
python -X utf8 inject_session.py
```

Salida esperada:
```
[1] Cache cargado: 55 cookies, 34 localStorage keys
[2] Page ID destino: 060AA06CBD5F94BF...
[3] Inyectando 55 cookies...
    Inyectadas: 51 | Fallidas: 0 | Expiradas: 4
[4] Restaurando 34 localStorage keys (reconectando)...
    localStorage OK
[5] Navegando a chatgpt.com...
    Navegado OK
[DONE] Sesion de ChatGPT inyectada en TEST_COOKIE1.
```

### Flujo completo cross-profile

```
1. Abrir perfil fuente (#1 Chat Gpt Pro)
   POST /profiles/open  {"name": "#1 Chat Gpt Pro", "timeout": 90}

2. Extraer su sesión al cache
   POST /cookies/%231%20Chat%20Gpt%20Pro/extract?port=52101

3. Abrir perfil destino (TEST_COOKIE1)
   POST /profiles/open  {"name": "TEST_COOKIE1", "timeout": 90}

4. Inyectar sesión del perfil fuente en el destino
   python inject_session.py
   (o usar restore apuntando al cache del perfil fuente)
```

---

## Uso desde Python (CookieManager directo)

```python
import sys
sys.path.insert(0, r"C:\Users\NyGsoft\Desktop\publicidad_david\server_dicloak")
from cookies_dicloak import CookieManager

mgr = CookieManager()

# Extraer y guardar cookies del perfil abierto
info = mgr.extract_and_save(port=52101, profile_name="#1 Chat Gpt Pro")
print(info.session_expires_in_days)   # días hasta expirar
print(info.needs_rewarm)              # True si expira en < 7 días

# Ver estado sin abrir el navegador
info = mgr.session_info("#1 Chat Gpt Pro")
print(info.is_session_valid)

# Restaurar cookies en un perfil abierto
result = mgr.restore(port=53706, profile_name="#1 Chat Gpt Pro")
print(result["restored_cookies"])    # cuántas se inyectaron

# Listar todos los perfiles con cache
for p in mgr.list_profiles():
    print(p["profile_name"], "→ válida:", p["session_valid"])
```

---

## Analogía con el RPA de la Policía

| RPA Policía | DICloak Cache |
|---|---|
| `captchaResueltaAt` (token 110s) | `__Secure-next-auth.session-token` (~90 días) |
| `viewstate` keep-alive | `cf_clearance` (~365 días) |
| rewarm a los 95s | `needs_rewarm` si < 7 días |
| cache en memoria | `cache/cookies/{Perfil}.json` |
| token por sesión | sesión reutilizable entre perfiles |

---

## Notas importantes

- El puerto CDP **cambia** cada vez que se abre un perfil — siempre consultar `/profiles/running`.
- `inject_shortcuts()` se llama automáticamente en cada `open_profile` (F12 habilitado).
- La inyección cross-profile reconecta el WebSocket tras el reload que causan las cookies.
- Las cookies con `expires < now` se saltan automáticamente al restaurar.
- La librería requerida es `websockets` (NO `websocket-client` — da 403 en ginsbrowser).

---

## POST `/cookies/inject-json?port={cdp_port}` ⭐ nuevo

Recibe directamente un JSON de cache e inyecta la sesión en el perfil abierto.
**No requiere que el cache esté guardado en disco** — se puede enviar desde cualquier PC.

```bash
# Inyectar el cache de #1 Chat Gpt Pro en TEST_COOKIE1 abierto en puerto 53706
curl -X POST "http://127.0.0.1:8585/cookies/inject-json?port=53706" \
     -H "Content-Type: application/json" \
     -d @"cache/cookies/#1_Chat_Gpt_Pro.json"
```

Respuesta:
```json
{
  "success": true,
  "message": "Sesion inyectada: 51 cookies, 34 localStorage keys",
  "data": {
    "port": 53706,
    "cookies_injected": 51,
    "cookies_failed": 0,
    "cookies_skipped_expired": 4,
    "localStorage_keys_restored": 34
  }
}
```

### Flujo cross-PC (compartir sesión entre máquinas)

```bash
# PC A — exportar sesión
curl http://PC-A:8585/cookies/%231%20Chat%20Gpt%20Pro/extract?port=52101 -X POST
curl http://PC-A:8585/cache/cookies/#1_Chat_Gpt_Pro.json > sesion.json

# PC B — abrir perfil e inyectar sesión recibida
curl -X POST http://PC-B:8585/profiles/open -d '{"name":"TEST_COOKIE1","timeout":90}'
curl -X POST "http://PC-B:8585/cookies/inject-json?port=53706" \
     -H "Content-Type: application/json" -d @sesion.json
```
