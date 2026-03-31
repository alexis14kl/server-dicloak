"""
DICloak API Client — Control de perfiles via REST API.

Reemplaza el scraping de la UI de DICloak (900+ líneas JS)
por llamadas HTTP directas a la Open API local.

Requiere:
  - DICloak con Open API activada (Settings → Open API)
  - DICLOAK_API_PORT en .env (default: 52140)
  - DICLOAK_API_KEY en .env
"""

from core.dicloak_api.api import DICloakAPI

__all__ = ["DICloakAPI"]
