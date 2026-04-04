"""Interceptar TODOS los ipcRenderer.invoke/send para ver canales de DNS."""
import json, sys, os, io, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cdp_bridge import cdp_evaluate_sync

port = 9333

# Interceptar TODO
intercept_js = r"""(() => {
    if (window.__ALL_LOG__) return 'ALREADY';
    window.__ALL_LOG__ = [];

    const { ipcRenderer } = require('electron');

    const _origInvoke = ipcRenderer.invoke.bind(ipcRenderer);
    ipcRenderer.invoke = function(channel, ...args) {
        window.__ALL_LOG__.push({
            type: 'invoke',
            channel,
            argsPreview: JSON.stringify(args).substring(0, 500),
            ts: Date.now(),
        });
        return _origInvoke(channel, ...args);
    };

    const _origSend = ipcRenderer.send.bind(ipcRenderer);
    ipcRenderer.send = function(channel, ...args) {
        window.__ALL_LOG__.push({
            type: 'send',
            channel,
            argsPreview: JSON.stringify(args).substring(0, 500),
            ts: Date.now(),
        });
        return _origSend(channel, ...args);
    };

    return 'ALL_INTERCEPTOR_INSTALLED';
})()"""

result = cdp_evaluate_sync(intercept_js, port, timeout=5)
print(f"Interceptor: {result}")
print("\n>>> ABRE UN PERFIL MANUALMENTE EN DICLOAK AHORA <<<")
print("Esperando 20s...\n")
time.sleep(20)

# Leer logs
logs = cdp_evaluate_sync("JSON.stringify(window.__ALL_LOG__ || [], null, 2)", port, timeout=5)
if logs:
    items = json.loads(logs)
    print(f"Total eventos: {len(items)}\n")
    for item in items:
        ch = item.get('channel', '')
        preview = item.get('argsPreview', '')[:200]
        print(f"  [{item.get('type')}] {ch}")
        if 'dns' in preview.lower() or 'dns' in ch.lower() or 'open' in ch.lower() or 'env' in ch.lower():
            print(f"    >>> {preview}")
