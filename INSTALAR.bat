@echo off
rem Instalador del Copiloto SPIN: doble clic y listo. Sin tildes a proposito
rem (la consola de Windows las muestra mal).
chcp 65001 >nul
title Instalar Copiloto SPIN
cd /d "%~dp0copiloto-spin"

echo.
echo  ==========================================
echo    Instalando el Copiloto SPIN
echo  ==========================================
echo.

rem --- 1) Python 3.12 -------------------------------------------------
set "PY="
py -3.12 --version >nul 2>&1 && set "PY=py -3.12"
if not defined PY (
    python --version 2>nul | findstr /c:" 3.12" >nul && set "PY=python"
)
if not defined PY (
    echo  [X] No encuentro Python 3.12 en este computador.
    echo.
    echo      1. Se va a abrir la pagina de descarga.
    echo      2. Baja "Windows installer (64-bit)" e instalalo.
    echo      3. MUY IMPORTANTE: marca la casilla "Add python.exe to PATH".
    echo      4. Vuelve a darle doble clic a INSTALAR.bat
    echo.
    start "" "https://www.python.org/downloads/release/python-3129/"
    pause
    exit /b 1
)
echo  [OK] Python 3.12 encontrado.

rem --- 2) Dependencias ------------------------------------------------
echo.
echo  Instalando lo que necesita el programa (1-2 minutos)...
%PY% -m pip install --quiet --disable-pip-version-check -r requirements.txt
if errorlevel 1 (
    echo.
    echo  [X] Fallo la instalacion. Revisa tu internet y vuelve a intentar.
    pause
    exit /b 1
)
echo  [OK] Listo.

rem --- 3) Archivo de claves -------------------------------------------
if not exist ".env" (
    copy /y ".env.example" ".env" >nul
    echo.
    echo  Se va a abrir el archivo de claves en el Bloc de notas.
    echo  Pega tus dos claves ^(Deepgram y Anthropic^), guarda con Ctrl+G y cierralo.
    echo  Las instrucciones para sacar las claves estan dentro del archivo.
    echo.
    pause
    notepad ".env"
) else (
    echo  [OK] Ya tienes tu archivo de claves ^(.env^).
)

echo.
echo  ==========================================
echo    Instalacion terminada.
echo    Para usarlo: doble clic en "ABRIR COPILOTO.bat"
echo  ==========================================
echo.
pause
