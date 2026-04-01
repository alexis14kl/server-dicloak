"""Debug: ver videos en el proyecto de Flow."""
import json, sys, os, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from chat_veo3_videos.veo3_session import Veo3Session

port = int(sys.argv[1]) if len(sys.argv) > 1 else 56208
s = Veo3Session(port=port)
s.connect()

url = s.evaluate("window.location.href") or ""
title = s.evaluate("document.title") or ""
print(f"URL: {url[:80]}")
print(f"Title: {title}")

# Ver videos
videos = s.evaluate("""(() => {
    const videos = Array.from(document.querySelectorAll('video'));
    return JSON.stringify(videos.map(v => ({
        src: (v.src || v.currentSrc || '').substring(0, 120),
        readyState: v.readyState,
        width: v.videoWidth,
        height: v.videoHeight,
        duration: isNaN(v.duration) ? 0 : v.duration,
        visible: v.offsetWidth > 0,
        w: v.offsetWidth, h: v.offsetHeight,
    })), null, 2);
})()""")
print(f"\nVideos ({len(json.loads(videos or '[]'))}):")
print(videos)

# Ver botones de descarga
btns = s.evaluate("""(() => {
    const btns = Array.from(document.querySelectorAll('button'));
    return JSON.stringify(btns.filter(b => {
        const t = (b.innerText||'').toLowerCase();
        return (t.includes('download') || t.includes('descargar')) && b.offsetWidth > 0;
    }).map(b => ({text: b.innerText.trim().substring(0,40), w: b.offsetWidth})), null, 2);
})()""")
print(f"\nBotones download: {btns}")

s.close()
