import json, urllib.request, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from chat_veo3_videos.veo3_session import Veo3Session

port = 54131
session = Veo3Session(port=port)

if not session.connect():
    print("No conectó")
    sys.exit(1)

# Ver URL actual
url = session.evaluate("window.location.href") or ""
print(f"URL: {url}")

# Ver título
title = session.evaluate("document.title") or ""
print(f"Titulo: {title}")

# Ver texto y botones visibles
info = session.evaluate("""(() => {
    const buttons = Array.from(document.querySelectorAll('button, a, [role="button"]'));
    const btnTexts = buttons.map(b => ({
        tag: b.tagName,
        text: (b.innerText || b.getAttribute('aria-label') || '').trim().substring(0, 60),
        href: (b.href || '').substring(0, 80),
    })).filter(b => b.text);

    return JSON.stringify({
        bodyText: (document.body?.innerText || '').substring(0, 500),
        buttons: btnTexts.slice(0, 15),
    }, null, 2);
})()""")

print(f"\nInfo de la página:")
print(info)

session.close()
