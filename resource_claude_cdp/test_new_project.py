import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from chat_veo3_videos.veo3_session import open_new_project

port = int(sys.argv[1]) if len(sys.argv) > 1 else 65024
result = open_new_project(port)
print(json.dumps(result, indent=2, ensure_ascii=False))
