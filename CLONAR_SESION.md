# Clonar Sesión entre Perfiles DICloak

Copia la sesión activa de cualquier servicio a otro perfil sin volver a loguearse.
Funciona para cualquier sitio: ChatGPT, DeepSeek, Leonardo AI, Meta AI, CapCut, etc.

---

## Casos de éxito validados

| Fuente | Destino | Sitio | Resultado |
|---|---|---|---|
| Leonardo AI | #1 Chat Gpt Pro | app.leonardo.ai | Sesión activa ✅ |
| #2 Meta AI | #1 Chat Gpt Pro | meta.ai | Sesión activa ✅ |
| TEST_COOKIE1 (DeepSeek) | #1 Chat Gpt Pro | chat.deepseek.com | Sesión activa ✅ |

---

## Flujo completo

### Paso 1 — Abrir el perfil fuente y extraer su sesión

```
curl -X POST http://127.0.0.1:8585/profiles/open -H "Content-Type: application/json" -d "{\"name\": \"#2 Meta AI\", \"timeout\": 90}"
```

Guardar el `debug_port` que devuelve (ej: 57978).

```
curl -X POST http://127.0.0.1:8585/cookies/extract?port=57978
```

Guarda la sesión en `cache\cookies\#2_Meta_AI.json`.

Si el perfil fue abierto manualmente en DICloak (no via API), mandar el nombre explícito:

```
curl -X POST http://127.0.0.1:8585/cookies/extract -H "Content-Type: application/json" -d "{\"port\": 57978, \"name\": \"#2 Meta AI\"}"
```

---

### Paso 2 — Abrir el perfil destino

```
curl -X POST http://127.0.0.1:8585/profiles/open -H "Content-Type: application/json" -d "{\"name\": \"#1 Chat Gpt Pro\", \"timeout\": 90}"
```

Guardar el nuevo `debug_port` (ej: 58679).

---

### Paso 3 — Inyectar la sesión en el perfil destino

```
curl -X POST http://127.0.0.1:8585/cookies/inject -H "Content-Type: application/json" -d "{\"port\": 58679, \"file\": \"#2_Meta_AI.json\"}"
```

Respuesta esperada:

```json
{
  "success": true,
  "message": "Sesion inyectada desde '#2_Meta_AI.json': 96 cookies, 65 localStorage keys",
  "data": {
    "port": 58679,
    "file": "#2_Meta_AI.json",
    "navigated_to": "https://meta.ai",
    "cookies_injected": 96,
    "cookies_failed": 0,
    "cookies_skipped_expired": 1,
    "localStorage_keys_restored": 65
  }
}
```

El perfil destino navega automáticamente al sitio correcto con la sesión activa.

---

## Cómo funciona la detección automática del sitio destino

El sistema detecta a dónde navegar **desde los dominios reales de las cookies** del archivo — sin mapas hardcodeados. Si las cookies son de `chat.deepseek.com`, navega a DeepSeek. Si son de `chatgpt.com`, navega a ChatGPT. Funciona para cualquier servicio sin configuración extra.

Si querés forzar un destino específico, podés pasarlo en el body:

```
curl -X POST http://127.0.0.1:8585/cookies/inject -H "Content-Type: application/json" -d "{\"port\": 58679, \"file\": \"mi_sesion.json\", \"url\": \"https://chat.deepseek.com\"}"
```

---

## Comportamiento según el estado del perfil destino

| Situación | Qué hace el sistema |
|---|---|
| El perfil ya está en el dominio correcto | Setea localStorage y recarga la página |
| El perfil está en otro dominio | Registra script de localStorage + navega al sitio destino |

---

## Ver caches disponibles

```
curl http://127.0.0.1:8585/cookies/
```

---

## Ver perfiles corriendo y sus puertos

```
curl http://127.0.0.1:8585/profiles/running
```

---

## Notas

- El `debug_port` cambia cada vez que se abre un perfil — siempre consultar `/profiles/running`.
- Las cookies con fecha de expiración vencida se saltan automáticamente.
- La sesión se restaura completa: cookies + localStorage.
- Funciona entre perfiles del mismo o diferente servicio.
- Meta AI puede mostrar "Vercel Security Checkpoint" en perfiles que nunca visitaron ese sitio — es una protección anti-bot por fingerprint, no un bug del sistema.
