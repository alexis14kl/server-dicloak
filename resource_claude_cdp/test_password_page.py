import json, time, sys, os, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from chat_veo3_videos.veo3_session import Veo3Session

port = 55377
s = Veo3Session(port=port)
s.connect()

url = s.evaluate("window.location.href") or ""
print(f"URL: {url[:80]}")

# Si está en account chooser, hacer click en la cuenta
if "accountchooser" in url.lower():
    print("En account chooser. Click en cuenta...")
    s.evaluate("""(() => {
        const byId = document.querySelector('[data-identifier]');
        if (byId) byId.click();
    })()""")
    time.sleep(5)
    url = s.evaluate("window.location.href") or ""
    print(f"URL después: {url[:80]}")

title = s.evaluate("document.title") or ""
print(f"Titulo: {title}")

# Ver todo lo que hay en la página de contraseña
info = s.evaluate("""(() => {
    const result = {inputs: [], buttons: [], tooltips: [], iframes: []};

    document.querySelectorAll('input').forEach(i => {
        result.inputs.push({
            type: i.type, name: i.name, id: i.id,
            visible: i.offsetWidth > 0,
            width: i.offsetWidth, height: i.offsetHeight,
        });
    });

    document.querySelectorAll('button, [role="button"]').forEach(b => {
        const t = (b.innerText || '').trim();
        if (t) result.buttons.push({text: t.substring(0, 50), visible: b.offsetWidth > 0});
    });

    document.querySelectorAll('[role="tooltip"], [role="listbox"], [role="option"], [role="menu"], [role="menuitem"], [role="dialog"], [data-tooltip], [aria-haspopup]').forEach(el => {
        result.tooltips.push({
            tag: el.tagName, role: el.getAttribute('role'),
            text: (el.innerText || '').substring(0, 60),
            visible: el.offsetWidth > 0,
            ariaPopup: el.getAttribute('aria-haspopup'),
        });
    });

    document.querySelectorAll('iframe').forEach(f => {
        result.iframes.push({src: (f.src || '').substring(0, 80), visible: f.offsetWidth > 0});
    });

    return JSON.stringify(result, null, 2);
})()""")

print(info)

# Ahora hacer click en el campo de password
print("\n=== Click en campo password ===")
r = s.evaluate("""(() => {
    const pwd = document.querySelector('input[type="password"]');
    if (!pwd) return 'NO_PASSWORD_FIELD';
    pwd.focus();
    pwd.click();
    return 'CLICKED visible=' + (pwd.offsetWidth > 0) + ' w=' + pwd.offsetWidth;
})()""")
print(f"Password click: {r}")

time.sleep(2)

# Ver si apareció un tooltip/popup
popup = s.evaluate("""(() => {
    const tooltips = document.querySelectorAll('[role="tooltip"], [role="listbox"], [role="option"], [role="dialog"], [role="presentation"], .gaia-tooltip, [data-is-tooltip]');
    const popups = Array.from(tooltips).filter(el => el.offsetWidth > 0);
    if (popups.length === 0) {
        // Buscar cualquier elemento nuevo visible que parezca popup
        const all = document.querySelectorAll('div');
        const candidates = Array.from(all).filter(el => {
            const style = window.getComputedStyle(el);
            return style.position === 'absolute' || style.position === 'fixed';
        }).filter(el => el.offsetWidth > 0 && el.offsetHeight > 20);
        return JSON.stringify({popups: 0, absoluteDivs: candidates.map(el => ({
            text: (el.innerText || '').substring(0, 80),
            class: (el.className || '').substring(0, 40),
            pos: el.getBoundingClientRect(),
        })).slice(0, 5)});
    }
    return JSON.stringify({popups: popups.map(el => ({
        role: el.getAttribute('role'),
        text: (el.innerText || '').substring(0, 80),
    }))});
})()""")
print(f"\nPopups después del click: {popup}")

s.close()
