"""Buscar imágenes en ChatGPT - búsqueda amplia."""
import json, sys, os, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from chat_gpt_consulta.prompt_paste import ChatGPTSession

port = int(sys.argv[1]) if len(sys.argv) > 1 else 49338
s = ChatGPTSession(port=port)
s.connect()

# Buscar TODAS las imágenes grandes en la página
images = s.evaluate("""(() => {
    const all = document.querySelectorAll('img');
    return JSON.stringify(Array.from(all).filter(img => {
        return img.naturalWidth > 100 || img.offsetWidth > 100;
    }).map((img, i) => ({
        index: i,
        src: (img.src || '').substring(0, 200),
        alt: (img.alt || '').substring(0, 80),
        w: img.naturalWidth, h: img.naturalHeight,
        displayW: img.offsetWidth, displayH: img.offsetHeight,
        parentClass: (img.parentElement?.className || '').substring(0, 40),
        parentTag: img.parentElement?.tagName,
    })), null, 2);
})()""")
print(f"Imagenes grandes:\n{images}")

# Buscar el contenedor de los botones "Descargar esta imagen"
containers = s.evaluate("""(() => {
    const btns = Array.from(document.querySelectorAll('button[aria-label*="Descargar"], button[aria-label*="Download"]'));
    return JSON.stringify(btns.map((btn, i) => {
        // Subir en el DOM para encontrar la imagen asociada
        let parent = btn.parentElement;
        let img = null;
        for (let j = 0; j < 10; j++) {
            if (!parent) break;
            img = parent.querySelector('img');
            if (img && img.naturalWidth > 100) break;
            img = null;
            parent = parent.parentElement;
        }

        // También buscar background-image
        let bgImg = null;
        if (!img && parent) {
            const allDivs = parent.querySelectorAll('div');
            for (const div of allDivs) {
                const bg = window.getComputedStyle(div).backgroundImage;
                if (bg && bg !== 'none') {
                    bgImg = bg.substring(0, 150);
                    break;
                }
            }
        }

        return {
            btnIndex: i,
            imgSrc: img ? img.src.substring(0, 200) : null,
            imgW: img?.naturalWidth || 0,
            imgH: img?.naturalHeight || 0,
            bgImage: bgImg,
            parentHTML: (parent?.innerHTML || '').substring(0, 200),
        };
    }), null, 2);
})()""")
print(f"\nContenedores de descarga:\n{containers}")

# Buscar URLs de imágenes en el DOM (data-testid, data-src, etc)
urls = s.evaluate("""(() => {
    const result = [];
    // Buscar elementos con src que parezcan imágenes generadas
    const allElements = document.querySelectorAll('[src], [data-src]');
    for (const el of allElements) {
        const src = el.getAttribute('src') || el.getAttribute('data-src') || '';
        if (src.includes('oaidalleapiprodscus') || src.includes('estuary')
            || src.includes('dall-e') || src.includes('openai')) {
            result.push({tag: el.tagName, src: src.substring(0, 200)});
        }
    }
    // Buscar en conversation turns
    const turns = document.querySelectorAll('[data-message-id]');
    for (const turn of turns) {
        const role = turn.getAttribute('data-message-author-role');
        const imgs = turn.querySelectorAll('img');
        if (imgs.length > 0) {
            result.push({role, imgsCount: imgs.length,
                firstSrc: (imgs[0].src || '').substring(0, 200)});
        }
    }
    return JSON.stringify(result, null, 2);
})()""")
print(f"\nURLs de imagenes OpenAI:\n{urls}")

s.close()
