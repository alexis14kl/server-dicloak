"""Call veo3/stabilize API."""
import json, sys, urllib.request

port = int(sys.argv[1]) if len(sys.argv) > 1 else 60229
data = json.dumps({"port": port, "timeout": 90}).encode()
req = urllib.request.Request(
    "http://127.0.0.1:8585/veo3/stabilize",
    data=data,
    headers={"Content-Type": "application/json"},
    method="POST",
)
try:
    resp = urllib.request.urlopen(req, timeout=120)
    print(json.dumps(json.loads(resp.read()), indent=2, ensure_ascii=False))
except urllib.error.HTTPError as e:
    print(f"HTTP {e.code}")
    print(json.dumps(json.loads(e.read()), indent=2, ensure_ascii=False))
except Exception as e:
    print(f"Error: {e}")
