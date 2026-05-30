@echo off
setlocal enabledelayedexpansion
title Pulse PowerShell ReAct Agent

:: Set active console code page to UTF-8
chcp 65001 >nul

:: Enable ANSI / VT100 escape code processing in this cmd window
:: (required for rich colored output to render correctly)
reg add HKCU\Console /v VirtualTerminalLevel /t REG_DWORD /d 1 /f >nul 2>&1

echo.
echo ::===============================================::
echo       Pulse PowerShell ReAct Agent Launcher
echo ::===============================================::
echo.

:: Change to the directory where this .bat lives
cd /d "%~dp0"

:: 1. Check Python 3.10 via py launcher
echo [*] Checking Python 3.10 installation...
py -3.10 --version >nul 2>&1
if errorlevel 1 (
    echo [!] Error: Python 3.10 is not found via the py launcher.
    echo [*] Please install Python 3.10 and rerun this script.
    pause
    exit /b 1
)

:: 2. Virtual Environment Setup
if not exist ".venv\" (
    echo [*] Creating isolated Python 3.10 Virtual Environment (.venv^)...
    py -3.10 -m venv .venv
    if not exist ".venv\" (
        echo [!] Error: Failed to create Python virtual environment.
        pause
        exit /b 1
    )
    echo [+] Virtual environment created successfully.
)

:: 3. Activate Virtual Environment
echo [*] Activating virtual environment...
call .venv\Scripts\activate.bat
if errorlevel 1 (
    echo [!] Warning: Could not activate virtual environment. Continuing anyway.
)

:: 4. Install / Upgrade Dependencies
echo [*] Upgrading pip...
python -m pip install --upgrade pip --quiet

echo [*] Checking and installing dependencies...
python -m pip install "ollama>=0.2.0" "mcp>=1.20.0" "pyyaml>=6.0.0" "rich>=13.7.0" "pyfiglet>=1.0.2" --quiet

:: 5. Set Required Environment Variables
:: PYTHONIOENCODING / PYTHONUTF8 : force UTF-8 I/O (prevents charmap errors on Windows)
:: PYTHONUNBUFFERED : bypass output buffering (fixes display/blank screen freezes)
:: TERM : tells rich to use ANSI renderer instead of legacy Win32 console API
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
set PYTHONUNBUFFERED=1
set TERM=xterm-256color

echo [+] Setup complete. Launching interactive console...

:: Clear screen so the agent banner is the first thing visible
cls

:: 6. Launch in unbuffered mode
python -u console.py

echo.
echo [!] Session finished.
pause
