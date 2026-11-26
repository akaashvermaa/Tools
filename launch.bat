@echo off
setlocal
cd /d "%~dp0" || exit /b 1
set "AURA_BATCH=1"

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -c "import pip" >nul 2>nul
    if not errorlevel 1 goto use_venv
)

py -3 -c "import sys; sys.exit(sys.version_info < (3, 9))" >nul 2>nul
if not errorlevel 1 goto use_py
python -c "import sys; sys.exit(sys.version_info < (3, 9))" >nul 2>nul
if not errorlevel 1 goto use_python
echo Aura Tools needs Python 3.9 or newer. Install Python from python.org,
echo select Add Python to PATH, then run launch.bat again.
pause
exit /b 1

:use_python
python launch.py --setup-only --update
goto done

:use_py
py -3 launch.py --setup-only --update
goto done

:use_venv
".venv\Scripts\python.exe" launch.py --setup-only --update

:done
set "status=%errorlevel%"
pause
exit /b %status%
