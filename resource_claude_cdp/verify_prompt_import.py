import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from chat_gpt_consulta.prompt_paste import ChatGPTSession, paste_and_send_prompt
print("OK - prompt_paste importa correctamente")
# Verificar que _send_raw existe
s = ChatGPTSession(port=0)
assert hasattr(s, '_send_raw'), "_send_raw no existe"
print("OK - _send_raw existe en ChatGPTSession")
