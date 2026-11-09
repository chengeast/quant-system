@echo off
setlocal
cd /d "%~dp0"

set PORT=8890
set BASE=http://127.0.0.1:%PORT%

echo ============================================================
echo  Quant Backtest Console - One-Click Launch
echo  Panel URL: %BASE%
echo  To stop: close this window, or run the stop .bat
echo ============================================================

where python >nul 2>nul
if errorlevel 1 goto nopy

REM Readiness and port-in-use checks use a real TCP connect, not a text-based port check - see P1.
powershell -NoProfile -Command "try { $c = New-Object Net.Sockets.TcpClient; $c.Connect('127.0.0.1',%PORT%); $c.Close(); exit 0 } catch { exit 1 }" >nul 2>&1
if not errorlevel 1 goto already

echo Starting local backend (dashboard\server.py) ...
start "quant-server" /min python dashboard\server.py --port %PORT%

set WAIT=0
:loop
timeout /t 1 /nobreak >nul
set /a WAIT+=1
powershell -NoProfile -Command "try { $c = New-Object Net.Sockets.TcpClient; $c.Connect('127.0.0.1',%PORT%); $c.Close(); exit 0 } catch { exit 1 }" >nul 2>&1
if not errorlevel 1 goto open
if %WAIT% LSS 15 goto loop
echo [ERROR] Port %PORT% not reachable after %WAIT% s; backend may have failed to start.
timeout /t 2
exit /b 1

:open
start "" "%BASE%"
echo Panel opened.
echo To stop the backend: run the stop .bat, or end the python process manually.
timeout /t 2
exit /b 0

:already
echo [NOTE] Port %PORT% is already in use (server likely already running).
echo        Opening browser directly at: %BASE%
start "" "%BASE%"
pause
exit /b 0

:nopy
echo [ERROR] python not found in PATH.
echo         Install Python 3.9+ and make sure it is on PATH, then retry.
pause
exit /b 1
