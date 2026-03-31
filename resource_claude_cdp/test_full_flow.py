"""Test completo: navigate_and_stabilize con OS click autofill."""
import sys, os, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from chat_veo3_videos.veo3_session import navigate_and_stabilize

port = int(sys.argv[1]) if len(sys.argv) > 1 else 57382
print(f"=== Test navigate_and_stabilize en puerto {port} ===\n")

result = navigate_and_stabilize(port=port, timeout=90)
print(f"\n=== RESULTADO ===")
print(json.dumps(result, indent=2, ensure_ascii=False))
