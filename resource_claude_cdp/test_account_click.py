"""Debug: qué pasa exactamente al hacer click en data-identifier."""
import json, time, sys, os, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from chat_veo3_videos.veo3_session import Veo3Session

port = int(sys.argv[1]) if len(sys.argv) > 1 else 59471

s = Veo3Session(port=port)
s.connect()

url = s.evaluate("window.location.href") or ""
print(f"URL antes: {url[:80]}")

# Ver qué hay en la página
accounts = s.evaluate("""(() => {
    const els = document.querySelectorAll('[data-identifier]');
    return Array.from(els).map(el => ({
        id: el.getAttribute('data-identifier'),
        tag: el.tagName,
        text: (el.innerText || '').substring(0, 60),
        visible: el.offsetWidth > 0,
    }));
})()""")
print(f"Cuentas: {accounts}")

# Click y observar
print("\n=== Click en data-identifier ===")
r = s.evaluate("""(() => {
    const el = document.querySelector('[data-identifier]');
    if (!el) return 'NO_ELEMENT';
    el.click();
    return 'CLICKED: ' + el.getAttribute('data-identifier');
})()""")
print(f"Click: {r}")

# Esperar y ver qué pasó
for i in range(8):
    time.sleep(1)
    # Forzar reconexión si se perdió
    if not s.is_connected():
        print(f"  {i+1}s: WebSocket desconectado, reconectando...")
        s.connect()

    url = s.evaluate("window.location.href") or ""
    title = s.evaluate("document.title") or ""
    print(f"  {i+1}s: {title[:30]} | {url[:80]}")

    if "challenge/pwd" in url:
        print("  >>> PASSWORD PAGE!")
        break
    if "labs.google/fx" in url and "accounts" not in url:
        print("  >>> FLOW!")
        break

s.close()
