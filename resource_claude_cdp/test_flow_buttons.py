"""Ver botones disponibles en Flow."""
import json, sys, os, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from chat_veo3_videos.veo3_session import Veo3Session

port = int(sys.argv[1]) if len(sys.argv) > 1 else 65024
s = Veo3Session(port=port)
s.connect()

print(f"URL: {s.evaluate('window.location.href')}")
print(f"Title: {s.evaluate('document.title')}")

btns = s.evaluate("""(() => {
    const all = document.querySelectorAll('button, [role="button"], a[href]');
    return JSON.stringify(Array.from(all).map(el => {
        const rect = el.getBoundingClientRect();
        return {
            tag: el.tagName,
            text: (el.innerText || '').trim().substring(0, 60),
            ariaLabel: el.getAttribute('aria-label') || '',
            href: el.getAttribute('href') || '',
            visible: rect.width > 0,
            w: Math.round(rect.width), h: Math.round(rect.height),
        };
    }).filter(b => b.visible && (b.text || b.ariaLabel)), null, 2);
})()""")
print(f"\nBotones visibles:\n{btns}")

s.close()
