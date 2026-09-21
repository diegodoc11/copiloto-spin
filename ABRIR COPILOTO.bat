@echo off
rem Abre el Copiloto SPIN. Si algo falla, la ventana negra se queda abierta
rem con el mensaje para que se pueda leer (o mandar captura).
chcp 65001 >nul
title Copiloto SPIN
cd /d "%~dp0copiloto-spin"

set "PY=python"
py -3.12 --version >nul 2>&1 && set "PY=py -3.12"

if not exist ".env" (
    echo  Falta instalar. Dale doble clic primero a INSTALAR.bat
    pause
    exit /b 1
)

%PY% -X utf8 copiloto.py %*
if errorlevel 1 (
    echo.
    echo  El copiloto se cerro con un error. Lee el mensaje de arriba:
    echo  casi siempre es una clave mal pegada en el archivo .env
    pause
)
