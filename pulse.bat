@echo off
set PULSE_APP_ROOT=%~dp0
if exist "%PULSE_APP_ROOT%.venv\Scripts\python.exe" (
    "%PULSE_APP_ROOT%.venv\Scripts\python.exe" -u "%PULSE_APP_ROOT%console.py" %*
) else (
    py -3.10 -u "%PULSE_APP_ROOT%console.py" %*
)
