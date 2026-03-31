================================
  DICloak Control API - Server
================================

Ejecutar servidor:

  python -m core.dicloak_api.server

Puerto por defecto: 8585
URL: http://127.0.0.1:8585

Endpoints:
  GET  /health              Estado de DICloak
  GET  /profiles            Lista perfiles
  GET  /profiles/running    Perfiles abiertos + puertos CDP
  POST /profiles/open       Abrir perfil {"name": "#1 Chat Gpt PRO"}
  POST /profiles/close      Cerrar perfiles
  POST /profiles/hook       Inyectar hook CDP

Matar servidor:
  taskkill /F /PID <pid>
