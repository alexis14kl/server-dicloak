import json, urllib.request, sys

port = int(sys.argv[1]) if len(sys.argv) > 1 else 54395
targets = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=3).read())

print(f"Total targets: {len(targets)}\n")
for i, t in enumerate(targets):
    print(f"{i+1}. [{t.get('type')}] {t.get('title','')[:50]}")
    print(f"   URL: {t.get('url','')[:80]}")
    print()
