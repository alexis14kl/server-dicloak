@echo off
cd /d "C:\Users\Monkey\Desktop\Servidor-Publicidad-V2\server-dicloak"

:: Cerrar instancias previas
taskkill /F /IM ngrok.exe >nul 2>&1

:: Iniciar ngrok en segundo plano (minimizado)
start "" /min ngrok http 8585

:: Esperar a que ngrok levante
timeout /t 5 /noqa >nul

:: Iniciar el servidor DICloak
python server.py
pause
