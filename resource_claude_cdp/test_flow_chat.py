"""Ver elementos del chat de Flow (textarea, inputs, botones)."""
import json, sys, os, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from chat_veo3_videos.veo3_session import Veo3Session

port = int(sys.argv[1]) if len(sys.argv) > 1 else 49388
s = Veo3Session(port=port)
s.connect()

url = s.evaluate("window.location.href") or ""
print(f"URL: {url[:100]}")
print(f"Title: {s.evaluate('document.title')}")

# Buscar inputs, textareas, contenteditable
inputs = s.evaluate("""(() => {
    const result = [];

    // Textareas
    document.querySelectorAll('textarea').forEach(el => {
        result.push({type: 'textarea', placeholder: el.placeholder,
            visible: el.offsetWidth > 0, w: el.offsetWidth, h: el.offsetHeight,
            id: el.id, class: (el.className||'').substring(0,40)});
    });

    // Inputs text
    document.querySelectorAll('input[type="text"], input:not([type])').forEach(el => {
        result.push({type: 'input', placeholder: el.placeholder,
            visible: el.offsetWidth > 0, w: el.offsetWidth, h: el.offsetHeight,
            id: el.id, class: (el.className||'').substring(0,40)});
    });

    // Contenteditable
    document.querySelectorAll('[contenteditable="true"]').forEach(el => {
        result.push({type: 'contenteditable', tag: el.tagName,
            visible: el.offsetWidth > 0, w: el.offsetWidth, h: el.offsetHeight,
            text: (el.innerText||'').substring(0,50),
            class: (el.className||'').substring(0,40)});
    });

    // Roles textbox
    document.querySelectorAll('[role="textbox"]').forEach(el => {
        result.push({type: 'role-textbox', tag: el.tagName,
            visible: el.offsetWidth > 0, w: el.offsetWidth, h: el.offsetHeight,
            class: (el.className||'').substring(0,40)});
    });

    return JSON.stringify(result, null, 2);
})()""")
print(f"\nInputs/textareas:\n{inputs}")

# Botones relevantes (enviar, generar, etc)
btns = s.evaluate("""(() => {
    const btns = Array.from(document.querySelectorAll('button, [role="button"]'));
    return JSON.stringify(btns.filter(b => b.offsetWidth > 0).map(b => ({
        text: (b.innerText||'').trim().substring(0,40),
        ariaLabel: b.getAttribute('aria-label') || '',
        disabled: b.disabled,
        w: b.offsetWidth, h: b.offsetHeight,
    })).filter(b => b.text || b.ariaLabel), null, 2);
})()""")
print(f"\nBotones:\n{btns}")

s.close()
