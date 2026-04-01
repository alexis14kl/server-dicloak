"""Test directo: descargar video de Flow."""
import json, sys, os, io, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from chat_veo3_videos.veo3_session import Veo3Session

port = int(sys.argv[1]) if len(sys.argv) > 1 else 56208
video_url = "https://labs.google/fx/api/trpc/media.getMediaUrlRedirect?name=b93473be-8d03-4b67-b39e-741794f861ba"

s = Veo3Session(port=port)
s.connect()

# Test 1: fetch dentro del browser
print("=== Test fetch+blob+base64 ===")
result = s.evaluate(f"""(async () => {{
    try {{
        const resp = await fetch('{video_url}');
        if (!resp.ok) return JSON.stringify({{error: 'HTTP ' + resp.status}});
        const blob = await resp.blob();
        return JSON.stringify({{
            size: blob.size,
            type: blob.type,
            status: resp.status,
            url: resp.url.substring(0, 100),
        }});
    }} catch(e) {{
        return JSON.stringify({{error: e.message}});
    }}
}})()""", timeout=60, await_promise=True)
print(f"  Resultado: {result}")

# Si funciona, descargar completo con base64
if result and "error" not in result:
    data = json.loads(result)
    size = data.get("size", 0)
    print(f"  Blob size: {size} bytes ({size/1024/1024:.1f}MB)")

    if size > 100000:
        print("  Descargando base64...")
        b64 = s.evaluate(f"""(async () => {{
            const resp = await fetch('{video_url}');
            const blob = await resp.blob();
            return new Promise((resolve, reject) => {{
                const reader = new FileReader();
                reader.onload = () => resolve(reader.result.split(',')[1]);
                reader.onerror = reject;
                reader.readAsDataURL(blob);
            }});
        }})()""", timeout=180, await_promise=True)

        if b64:
            import base64
            video_bytes = base64.b64decode(b64)
            out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_video.mp4")
            with open(out_path, "wb") as f:
                f.write(video_bytes)
            print(f"  Guardado: {out_path} ({len(video_bytes)/1024/1024:.1f}MB)")
        else:
            print(f"  Base64 fallo: {b64}")
else:
    # Test 2: urllib directo
    print("\n=== Test urllib directo ===")
    import urllib.request
    try:
        req = urllib.request.Request(video_url, headers={
            "User-Agent": "Mozilla/5.0",
        })
        with urllib.request.urlopen(req, timeout=30) as resp:
            print(f"  Status: {resp.status}")
            print(f"  URL final: {resp.url[:100]}")
            data = resp.read(1024)
            print(f"  Primeros bytes: {len(data)}")
    except Exception as e:
        print(f"  Error: {e}")

s.close()
