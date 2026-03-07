@echo off
chcp 65001 >nul
cd /d "%~dp0"

:: 使用 .venv 虚拟环境运行（如果存在）
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" desktop_app.py
) else (
    python desktop_app.py
)

if %errorlevel% neq 0 (
    echo.
    echo [错误] 程序异常退出，错误码: %errorlevel%
    pause
)
