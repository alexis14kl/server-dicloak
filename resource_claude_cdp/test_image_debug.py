import json, urllib.request, sys
sys.path.insert(0, ".")
from chat_gpt_consulta.prompt_paste import ChatGPTSession

port = 50920
session = ChatGPTSession(port=port)

if not session.connect():
    print("No conectó")
    sys.exit(1)

# Ver qué hay en la página
result = session.evaluate("""(() => {
    const turns = Array.from(document.querySelectorAll('[data-testid^="conversation-turn"]'));
    const messages = Array.from(document.querySelectorAll('[data-message-id]'));
    const articles = Array.from(document.querySelectorAll('article'));
    const allBlocks = turns.length > 0 ? turns : messages.length > 0 ? messages : articles;
    const last2 = allBlocks.slice(-2);

    const info = [];
    for (const block of last2) {
        const imgs = Array.from(block.querySelectorAll('img'));
        const imgUrls = imgs.map(img => (img.currentSrc || img.src || '').substring(0, 100));
        const hasDownload = Array.from(block.querySelectorAll('button,[role="button"],a'))
            .some(el => /descargar|download/i.test(el.innerText || el.getAttribute('aria-label') || ''));
        const hasOverlay = !!block.querySelector('[data-testid="image-gen-overlay-actions"]');
        const text = (block.innerText || '').substring(0, 100);

        info.push({
            imgs: imgUrls.length,
            imgUrls,
            hasDownload,
            hasOverlay,
            text: text.substring(0, 80),
        });
    }
    return JSON.stringify(info, null, 2);
})()""")

print("Últimos 2 bloques:")
print(result)

session.close()
