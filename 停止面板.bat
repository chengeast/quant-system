@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set PORT=8890

echo 正在停止统计套利控制台后端 (端口 %PORT%) ...
REM 找到监听该端口的 PID 并结束
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%PORT% " ^| findstr "LISTENING"') do (
    taskkill /PID %%a /T /F >nul 2>&1
    echo   已结束 PID %%a
)
echo 完成。
timeout /t 1
exit /b 0
