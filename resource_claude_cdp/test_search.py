import urllib.request, json

tests = [
    "/profiles",
    "/profiles/search/Chat Gpt PRO",
    "/profiles/search/chat gpt",
    "/profiles/search/noexiste",
]

for path in tests:
    url = f"http://127.0.0.1:8585{path.replace(' ', '%20')}"
    print(f"\n=== GET {path} ===")
    try:
        resp = urllib.request.urlopen(url, timeout=5)
        data = json.loads(resp.read())
        print(json.dumps(data, indent=2, ensure_ascii=False)[:300])
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"HTTP {e.code}: {body[:200]}")
    except Exception as e:
        print(f"Error: {e}")
