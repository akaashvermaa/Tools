@echo off
setlocal
cd /d "%~dp0" || exit /b 1

if not exist ".venv\Scripts\python.exe" (
    echo Aura Tools needs setup. Double-click launch.bat first, then run.bat.
    pause
    exit /b 1
)

set "AURA_BATCH=1"
".venv\Scripts\python.exe" launch.py --run-only %*
set "status=%errorlevel%"
if not "%status%"=="0" (
    echo If setup is missing or out of date, run launch.bat and try again.
    pause
)
exit /b %status%
