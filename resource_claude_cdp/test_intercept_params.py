"""Interceptar los params que DiCloak envia al abrir un perfil."""
import json, sys, os, io, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cdp_bridge import cdp_evaluate_sync

port = 9333

# Inyectar interceptor que loguea los params de ipcRenderer.invoke
intercept_js = r"""(() => {
    if (window.__PARAM_LOG__) return 'ALREADY';
    window.__PARAM_LOG__ = [];

    const { ipcRenderer } = require('electron');
    const _orig = ipcRenderer.invoke.bind(ipcRenderer);
    ipcRenderer.invoke = function(channel, ...args) {
        // Loguear solo channels relacionados con abrir perfil
        if (channel && (channel.includes('open') || channel.includes('env') || channel.includes('profile') || channel.includes('browser'))) {
            window.__PARAM_LOG__.push({
                channel,
                args: JSON.parse(JSON.stringify(args).substring(0, 2000)),
                ts: Date.now(),
            });
        }
        return _orig(channel, ...args);
    };
    return 'INTERCEPTOR_INSTALLED';
})()"""

result = cdp_evaluate_sync(intercept_js, port, timeout=5)
print(f"Interceptor: {result}")
print("\nAhora abre un perfil manualmente en DiCloak y espera 10s...")
time.sleep(15)

# Leer los params capturados
logs = cdp_evaluate_sync("JSON.stringify(window.__PARAM_LOG__ || [], null, 2)", port, timeout=5)
print(f"\nParams interceptados:\n{logs}")
