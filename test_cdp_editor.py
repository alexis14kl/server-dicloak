import json, urllib.request
import websockets.sync.client as ws_sync

port = 49715
targets = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=3).read())

for t in targets:
    if t.get("type") != "page" or "chatgpt.com" not in (t.get("url") or ""):
        continue

    ws_url = t.get("webSocketDebuggerUrl", "")
    print(f"\nProbando: {t.get('title','')} | {t.get('url','')[:60]}")

    try:
        ws = ws_sync.connect(ws_url, max_size=2**22)
        msg = json.dumps({
            "id": 1,
            "method": "Runtime.evaluate",
            "params": {
                "expression": """(() => {
                    const editor = document.querySelector('#prompt-textarea[contenteditable="true"]');
                    const title = document.title;
                    const ready = document.readyState;
                    const hasMain = !!document.querySelector('main');
                    return JSON.stringify({
                        title,
                        ready,
                        hasMain,
                        hasEditor: !!editor,
                        editorWidth: editor ? editor.getBoundingClientRect().width : 0,
                    });
                })()""",
                "returnByValue": True,
            }
        })
        ws.send(msg)
        resp = json.loads(ws.recv(timeout=5))
        value = resp.get("result", {}).get("result", {}).get("value", "")
        print(f"  Result: {value}")
        ws.close()
    except Exception as e:
        print(f"  Error: {e}")
