@echo off
set PULSE_APP_ROOT=%~dp0
py -3.10 -u "%PULSE_APP_ROOT%console.py" %*
