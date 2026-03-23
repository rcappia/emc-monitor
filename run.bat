@echo off
chcp 65001 > nul
title EMC Monitor — Radiodifusão

echo.
echo ============================================
echo   EMC Monitor — Sistema de Radiodifusão
echo ============================================
echo.

REM Caminho do Python instalado
set PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Python311\python.exe

if not exist "%PYTHON_EXE%" (
    echo [ERRO] Python nao encontrado.
    pause
    exit /b 1
)

REM Pega o IP local da rede
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /R "IPv4"') do (
    set LOCAL_IP=%%a
    goto :got_ip
)
:got_ip
set LOCAL_IP=%LOCAL_IP: =%

cd /d "%~dp0"

echo Sistema iniciando...
echo.
echo Acesse neste computador:  http://localhost:8001
echo Acesse na rede (Eduardo e Angelica): http://%LOCAL_IP%:8001
echo.
echo Para encerrar, feche esta janela.
echo.

REM Abre o navegador automaticamente
start /b cmd /c "timeout /t 2 > nul && start http://localhost:8001"

REM Inicia o servidor acessível na rede local (0.0.0.0 = todos os computadores da rede)
"%PYTHON_EXE%" -m uvicorn app.main:app --host 0.0.0.0 --port 8001

pause
