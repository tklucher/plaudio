@echo off
REM Builds dist\PlaudIO.exe on a Windows PC.
REM Needs Python 3.10+ from python.org (tick "Add python.exe to PATH" when installing).
setlocal
cd /d "%~dp0"

if not exist .venv (
    py -3 -m venv .venv || python -m venv .venv || goto :error
)
call .venv\Scripts\activate.bat || goto :error
python -m pip install --upgrade pip || goto :error
python -m pip install . "pyinstaller>=6" || goto :error

python packaging\build_exe.py || goto :error

echo.
echo Built dist\PlaudIO.exe
exit /b 0

:error
echo.
echo Build failed.
exit /b 1
