"""Click en botón Create (arrow_forward) del chat de Flow."""
import sys, os, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from chat_veo3_videos.veo3_session import Veo3Session

port = int(sys.argv[1]) if len(sys.argv) > 1 else 52280
s = Veo3Session(port=port)
s.connect()

# Ver el prompt actual
prompt = s.evaluate("document.querySelector('[contenteditable=\"true\"]')?.innerText || ''")
print(f"Prompt actual: {prompt}")

# Intentar click en Create
result = s.evaluate("""(() => {
    const btns = Array.from(document.querySelectorAll('button'));
    // Listar todos para debug
    const info = btns.filter(b => b.offsetWidth > 0).map(b => ({
        text: (b.innerText||'').trim().substring(0,30),
        disabled: b.disabled,
    }));

    // Buscar el boton arrow_forward Create
    const createBtn = btns.find(b => {
        const t = (b.innerText || '').trim();
        return t.includes('arrow_forward');
    });

    if (createBtn) {
        const disabled = createBtn.disabled;
        createBtn.click();
        return JSON.stringify({clicked: true, text: createBtn.innerText.trim().substring(0,30), disabled: disabled});
    }

    return JSON.stringify({clicked: false, buttons: info});
})()""")

print(f"Resultado: {result}")
s.close()
