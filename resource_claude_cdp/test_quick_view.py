import json, urllib.request, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from chat_veo3_videos.veo3_session import Veo3Session

port = int(sys.argv[1]) if len(sys.argv) > 1 else 54395
session = Veo3Session(port=port)

if not session.connect():
    print("No conecto")
    sys.exit(1)

url = session.evaluate("window.location.href") or ""
title = session.evaluate("document.title") or ""
print(f"URL: {url}")
print(f"Titulo: {title}")

info = session.evaluate("""(() => {
    const buttons = Array.from(document.querySelectorAll('button, a, [role="button"]'));
    const btnTexts = buttons.map(b => ({
        text: (b.innerText || '').trim().substring(0, 50),
        href: (b.href || '').substring(0, 60),
    })).filter(b => b.text).slice(0, 10);
    return JSON.stringify({
        bodyText: (document.body?.innerText || '').substring(0, 400),
        buttons: btnTexts,
    }, null, 2);
})()""")
print(info)
session.close()
