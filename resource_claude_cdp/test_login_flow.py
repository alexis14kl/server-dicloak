import json, time, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from chat_veo3_videos.veo3_session import Veo3Session

port = int(sys.argv[1]) if len(sys.argv) > 1 else 55077
s = Veo3Session(port=port)
s.connect()

print("=== Paso 1: Click en cuenta ===")
r = s.evaluate("""(() => {
    const byId = document.querySelector('[data-identifier]');
    if (byId) { byId.click(); return 'data-identifier: ' + byId.getAttribute('data-identifier'); }
    return 'NO_ACCOUNT';
})()""")
print(f"Resultado: {r}")

time.sleep(5)

print("\n=== Paso 2: Ver página después del click ===")
url = s.evaluate("window.location.href") or ""
title = s.evaluate("document.title") or ""
print(f"URL: {url[:100]}")
print(f"Titulo: {title}")

body = s.evaluate("(document.body?.innerText || '').substring(0, 300)") or ""
print(f"Body: {body[:200]}")

# Buscar campo de contraseña
pwd = s.evaluate("""(() => {
    const inputs = document.querySelectorAll('input');
    return Array.from(inputs).map(i => ({
        type: i.type, name: i.name, id: i.id, placeholder: i.placeholder,
        visible: i.offsetWidth > 0,
    }));
})()""")
print(f"\nInputs: {pwd}")

print("\n=== Paso 3: Click en campo password + doble click ===")
r2 = s.evaluate("""(() => {
    const pwd = document.querySelector('input[type="password"]');
    if (pwd) {
        pwd.focus();
        pwd.click();
        pwd.click(); // doble click
        return 'PASSWORD_CLICKED';
    }
    return 'NO_PASSWORD_FIELD';
})()""")
print(f"Resultado: {r2}")

time.sleep(3)

# Ver si DiCloak llenó la contraseña
pwd_val = s.evaluate("""(() => {
    const pwd = document.querySelector('input[type="password"]');
    return pwd ? 'length=' + pwd.value.length : 'NO_FIELD';
})()""")
print(f"Password value: {pwd_val}")

print("\n=== Paso 4: Click en Siguiente ===")
r3 = s.evaluate("""(() => {
    const btns = Array.from(document.querySelectorAll('button, [role="button"]'));
    const next = btns.find(b => {
        const t = (b.innerText || '').toLowerCase();
        return t.includes('next') || t.includes('siguiente') || t.includes('continuar');
    });
    if (next) { next.click(); return 'NEXT_CLICKED: ' + next.innerText.trim(); }
    return 'NO_NEXT_BUTTON';
})()""")
print(f"Resultado: {r3}")

time.sleep(5)

print("\n=== Estado final ===")
url = s.evaluate("window.location.href") or ""
title = s.evaluate("document.title") or ""
print(f"URL: {url[:100]}")
print(f"Titulo: {title}")

s.close()
