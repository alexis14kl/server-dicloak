"""Test: beforeinput + React internal state."""
import json, sys, os, io, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from chat_veo3_videos.veo3_session import Veo3Session

port = int(sys.argv[1]) if len(sys.argv) > 1 else 52280
prompt = "a dog on the beach"

s = Veo3Session(port=port)
s.connect()

safe = json.dumps(prompt)

# Limpiar
s.evaluate("""(() => {
    const editor = document.querySelector('[contenteditable="true"]');
    editor.focus();
    document.execCommand('selectAll');
    document.execCommand('delete');
})()""")
time.sleep(0.5)

# Metodo: beforeinput + insertText + input (lo que editores como Lexical esperan)
print("=== Metodo beforeinput ===")
r = s.evaluate(f"""(() => {{
    const editor = document.querySelector('[contenteditable="true"]');
    editor.focus();

    // beforeinput
    const beforeInput = new InputEvent('beforeinput', {{
        bubbles: true, cancelable: true,
        inputType: 'insertText', data: {safe},
    }});
    editor.dispatchEvent(beforeInput);

    // Insertar texto
    document.execCommand('insertText', false, {safe});

    // input
    const inputEv = new InputEvent('input', {{
        bubbles: true, inputType: 'insertText', data: {safe},
    }});
    editor.dispatchEvent(inputEv);

    return editor.innerText.length;
}})()""")
print(f"  Chars: {r}")
time.sleep(1)

# Probar click
s.evaluate("""(() => {
    const btns = Array.from(document.querySelectorAll('button'));
    const createBtn = btns.find(b => (b.innerText||'').includes('arrow_forward'));
    if (createBtn) createBtn.click();
})()""")
time.sleep(2)
body = s.evaluate("document.body?.innerText.substring(0, 300)") or ""
if "Prompt must" in body:
    print("  FALLO beforeinput")
else:
    print("  OK!")
    s.close()
    sys.exit(0)

# Metodo 2: Buscar React fiber y setear state
print("\n=== Metodo React fiber ===")
s.evaluate("""(() => {
    const editor = document.querySelector('[contenteditable="true"]');
    editor.focus();
    document.execCommand('selectAll');
    document.execCommand('delete');
})()""")
time.sleep(0.5)

r2 = s.evaluate(f"""(() => {{
    const editor = document.querySelector('[contenteditable="true"]');

    // Buscar React internal props
    const reactKey = Object.keys(editor).find(k => k.startsWith('__reactFiber') || k.startsWith('__reactInternalInstance') || k.startsWith('__reactProps'));
    if (!reactKey) return 'NO_REACT_KEY';

    const props = editor[reactKey];
    return JSON.stringify({{
        key: reactKey,
        type: typeof props,
        keys: props ? Object.keys(props).slice(0, 10) : [],
    }});
}})()""")
print(f"  React: {r2}")

# Metodo 3: Simular typing con keydown+beforeinput+input por cada char
print("\n=== Metodo char-by-char con beforeinput ===")
s.evaluate("""(() => {
    const editor = document.querySelector('[contenteditable="true"]');
    editor.focus();
    document.execCommand('selectAll');
    document.execCommand('delete');
})()""")
time.sleep(0.5)
s.evaluate("document.querySelector('[contenteditable=\"true\"]').focus()")
time.sleep(0.3)

for char in prompt:
    safe_char = json.dumps(char)
    s.evaluate(f"""(() => {{
        const editor = document.querySelector('[contenteditable="true"]');
        editor.dispatchEvent(new KeyboardEvent('keydown', {{key: {safe_char}, bubbles: true}}));
        editor.dispatchEvent(new InputEvent('beforeinput', {{
            bubbles: true, cancelable: true, inputType: 'insertText', data: {safe_char},
        }}));
        document.execCommand('insertText', false, {safe_char});
        editor.dispatchEvent(new InputEvent('input', {{
            bubbles: true, inputType: 'insertText', data: {safe_char},
        }}));
        editor.dispatchEvent(new KeyboardEvent('keyup', {{key: {safe_char}, bubbles: true}}));
    }})()""")
    time.sleep(0.01)

time.sleep(1)
content = s.evaluate("document.querySelector('[contenteditable=\"true\"]')?.innerText || ''")
print(f"  Contenido: '{content[:60]}'")

s.evaluate("""(() => {
    const btns = Array.from(document.querySelectorAll('button'));
    const createBtn = btns.find(b => (b.innerText||'').includes('arrow_forward'));
    if (createBtn) createBtn.click();
})()""")
time.sleep(2)
body = s.evaluate("document.body?.innerText.substring(0, 300)") or ""
if "Prompt must" in body:
    print("  FALLO char-by-char")
else:
    print("  OK!")

s.close()
