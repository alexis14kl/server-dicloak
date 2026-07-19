@echo off
cd /d "%~dp0"
setlocal enabledelayedexpansion

echo [1] Cerrando instancias previas...
taskkill /F /IM python.exe >nul 2>&1
taskkill /F /IM uvicorn.exe >nul 2>&1
timeout /t 2 >nul

echo [2] Liberando puerto 8585...
set TRIES=0
:waitloop
netstat -ano | findstr "LISTENING" | findstr ":8585" >nul 2>&1
if not errorlevel 1 (
    set /a TRIES+=1
    if !TRIES! GTR 10 goto forcekill
    for /f "tokens=5" %%a in ('netstat -ano ^| findstr "LISTENING" ^| findstr ":8585"') do (
        taskkill /F /PID %%a >nul 2>&1
    )
    timeout /t 1 >nul
    goto waitloop
)
goto startserver

:forcekill
echo [2b] Puerto 8585 ocupado por proceso no eliminable. Reintentando...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr "LISTENING" ^| findstr ":8585"') do (
    taskkill /F /PID %%a >nul 2>&1
)
timeout /t 3 >nul

:startserver
echo [3] Iniciando servidor DICloak...
python -X utf8 server.py
pause
