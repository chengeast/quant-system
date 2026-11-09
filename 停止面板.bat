@echo off
setlocal
cd /d "%~dp0"
set PORT=8890

echo Stopping quant backtest console backend (port %PORT%) ...
REM Kill by matching the server.py command line; the old text-based port check is unreliable on this host - see P1.
REM The Name filter excludes this PowerShell process itself, whose command line contains the text server.py.
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'python*' -and $_.CommandLine -like '*server.py*' } | ForEach-Object { taskkill /PID $_.ProcessId /T /F }" >nul 2>&1
echo Done.
timeout /t 1
exit /b 0
