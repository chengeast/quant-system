@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

set PORT=8890
set BASE=http://127.0.0.1:%PORT%

echo ============================================================
echo  统计套利回测控制台 · 一键启动
echo  面板地址: %BASE%
echo  停止: 关闭本窗口 或 Ctrl+C
echo ============================================================

REM 检查 python
where python >nul 2>nul
if errorlevel 1 (
    echo [错误] 未找到 python，请先安装 Python 3.9+ 并确保在 PATH。
    pause
    exit /b 1
)

REM 端口占用检查
netstat -ano | findstr ":%PORT% " | findstr "LISTENING" >nul
if not errorlevel 1 (
    echo [提示] 端口 %PORT% 已被占用（可能 server 已运行）。
    echo        直接打开浏览器: %BASE%
    start "" "%BASE%"
    pause
    exit /b 0
)

echo 正在启动本地后端 (dashboard\server.py) ...
start "quant-server" /min python dashboard\server.py --port %PORT%

REM 等待端口就绪（最多 15 秒）
set /a WAIT=0
:LOOP
timeout /t 1 /nobreak >nul
set /a WAIT+=1
netstat -ano | findstr ":%PORT% " | findstr "LISTENING" >nul
if not errorlevel 1 goto OPEN
if %WAIT% LSS 15 goto LOOP

echo [提示] 等待后端就绪较慢，仍尝试打开浏览器...
:OPEN
start "" "%BASE%"
echo 面板已打开。停止服务: 标题为 quant-server 的隐藏窗口按 Ctrl+C，或手动结束 python 进程。
timeout /t 2
exit /b 0
