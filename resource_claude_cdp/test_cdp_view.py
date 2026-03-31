import json, urllib.request

port = 49715
targets = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=3).read())
for t in targets:
    print(f"[{t.get('type')}] {t.get('title','')[:60]} | {t.get('url','')[:80]}")
