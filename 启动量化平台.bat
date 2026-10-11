@echo off
chcp 65001 >nul
title A股量化交易系统 v2.0
echo.
echo ╔══════════════════════════════════════╗
echo ║   A股量化交易系统 v2.0 启动中...     ║
echo ╚══════════════════════════════════════╝
echo.

cd /d "%~dp0"

:: Kill old server on port 8888
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8888') do taskkill /f /pid %%a >nul 2>&1

:: Start server
echo [1/2] 启动Web服务器...
start /b "" "G:\Hermes\hermes-agent\venv\Scripts\python.exe" web_server.py

:: Wait for server
echo [2/2] 等待服务就绪...
timeout /t 4 /nobreak >nul

:: Open browser
echo.
echo ✅ 启动成功! 正在打开浏览器...
start http://localhost:8888

echo.
echo ══════════════════════════════════════
echo   访问地址: http://localhost:8888
echo   API文档:  http://localhost:8888/docs
echo   关闭此窗口可停止服务器
echo ══════════════════════════════════════
echo.

:: Keep window open
pause
