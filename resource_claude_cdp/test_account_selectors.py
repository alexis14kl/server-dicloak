import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from chat_veo3_videos.veo3_session import Veo3Session

port = 54782
s = Veo3Session(port=port)
s.connect()

r = s.evaluate("""(() => {
    const results = [];

    // Buscar data-identifier
    document.querySelectorAll('[data-identifier]').forEach(el => {
        results.push({strategy: 'data-identifier', tag: el.tagName, id: el.getAttribute('data-identifier'), text: (el.innerText||'').substring(0,60)});
    });

    // Buscar data-email
    document.querySelectorAll('[data-email]').forEach(el => {
        results.push({strategy: 'data-email', tag: el.tagName, email: el.getAttribute('data-email'), text: (el.innerText||'').substring(0,60)});
    });

    // Buscar li con email
    document.querySelectorAll('li').forEach(el => {
        const t = el.innerText || '';
        if (t.includes('@')) {
            results.push({strategy: 'li-email', tag: 'LI', text: t.substring(0,80), clickable: true});
        }
    });

    // Buscar divs clickeables con email
    document.querySelectorAll('div[role="link"], div[tabindex], a').forEach(el => {
        const t = (el.innerText || '').trim();
        if (t.includes('@') && t.length < 100) {
            results.push({strategy: 'div-email', tag: el.tagName, text: t.substring(0,80), role: el.getAttribute('role')});
        }
    });

    // Buscar cualquier cosa con el email
    const allEls = document.querySelectorAll('*');
    for (const el of allEls) {
        if (el.children.length > 3) continue;
        const t = (el.innerText || '').trim();
        if (/@.*\\.com/.test(t) && t.length < 80 && !t.includes('\\n')) {
            results.push({strategy: 'any-email', tag: el.tagName, text: t, className: (el.className||'').substring(0,30)});
        }
    }

    return JSON.stringify(results, null, 2);
})()""")

print(r)
s.close()
