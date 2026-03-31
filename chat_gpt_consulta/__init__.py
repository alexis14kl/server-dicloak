"""
ChatGPT Consulta — Módulo para interactuar con ChatGPT via CDP.

Funcionalidades:
- Conectar a una o más instancias de ChatGPT via CDP
- Pegar prompts en el editor
- Enviar prompts (click en send)
- Esperar respuesta
- Extraer respuesta generada

Cada instancia de ChatGPT tiene su propio puerto CDP.
"""

from chat_gpt_consulta.prompt_paste import ChatGPTSession, paste_and_send_prompt

__all__ = ["ChatGPTSession", "paste_and_send_prompt"]
