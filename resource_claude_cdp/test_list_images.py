"""Ver qué imágenes hay en el chat de ChatGPT."""
import json, sys, os, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from chat_gpt_consulta.prompt_paste import ChatGPTSession

port = int(sys.argv[1]) if len(sys.argv) > 1 else 49338
s = ChatGPTSession(port=port)
s.connect()

url = s.evaluate("window.location.href") or ""
print(f"URL: {url[:80]}")

# Buscar imágenes en respuestas del asistente
images = s.evaluate("""(() => {
    const msgs = document.querySelectorAll('[data-message-author-role="assistant"]');
    const images = [];
    msgs.forEach((msg, msgIdx) => {
        const imgs = msg.querySelectorAll('img');
        imgs.forEach((img, imgIdx) => {
            const src = img.src || img.currentSrc || '';
            const alt = img.alt || '';
            const rect = img.getBoundingClientRect();
            if (src && rect.width > 50) {
                images.push({
                    messageIndex: msgIdx,
                    imageIndex: imgIdx,
                    src: src.substring(0, 200),
                    alt: alt.substring(0, 100),
                    width: Math.round(rect.width),
                    height: Math.round(rect.height),
                    naturalWidth: img.naturalWidth,
                    naturalHeight: img.naturalHeight,
                });
            }
        });
    });
    return JSON.stringify(images, null, 2);
})()""")
print(f"\nImagenes encontradas:\n{images}")

# También buscar links de descarga (backend-api/estuary)
downloads = s.evaluate("""(() => {
    const links = Array.from(document.querySelectorAll('a[href]'));
    const downloadLinks = links.filter(a => {
        const href = (a.href || '').toLowerCase();
        return href.includes('estuary') || href.includes('download') || href.includes('blob');
    });
    return JSON.stringify(downloadLinks.map(a => ({
        href: a.href.substring(0, 200),
        text: (a.innerText || '').trim().substring(0, 50),
    })), null, 2);
})()""")
print(f"\nLinks de descarga:\n{downloads}")

# Buscar botones de descarga en las imágenes
dlBtns = s.evaluate("""(() => {
    const btns = Array.from(document.querySelectorAll('button'));
    return JSON.stringify(btns.filter(b => {
        const text = (b.innerText || '').toLowerCase();
        const aria = (b.getAttribute('aria-label') || '').toLowerCase();
        return (text.includes('download') || text.includes('descargar')
            || aria.includes('download') || aria.includes('descargar'))
            && b.offsetWidth > 0;
    }).map(b => ({
        text: (b.innerText || '').trim().substring(0, 40),
        ariaLabel: b.getAttribute('aria-label') || '',
        w: b.offsetWidth,
    })), null, 2);
})()""")
print(f"\nBotones download:\n{dlBtns}")

s.close()
