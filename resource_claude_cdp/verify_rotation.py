import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from chat_gpt_consulta.prompt_paste import paste_and_send_with_rotation, ChatGPTSession
from chat_gpt_consulta.account_state import get_exhausted_ids, mark_exhausted, clear_exhausted
print("OK - rotation imports correctos")

# Verificar métodos nuevos
s = ChatGPTSession(port=0)
assert hasattr(s, 'detect_token_status'), "detect_token_status no existe"
assert hasattr(s, 'switch_account'), "switch_account no existe"
print("OK - detect_token_status y switch_account existen")
