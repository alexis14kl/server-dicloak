"""
Chat Veo3 Videos — Módulo para interactuar con Google Flow (Veo 3) via CDP.

Funcionalidades:
- Navegar a Veo 3 (labs.google/fx/tools/flow)
- Manejar login de Google automáticamente
- Verificar estabilidad del navegador
- Pegar prompts de video
- Descargar videos generados
"""

from chat_veo3_videos.veo3_session import Veo3Session, navigate_and_stabilize

__all__ = ["Veo3Session", "navigate_and_stabilize"]
