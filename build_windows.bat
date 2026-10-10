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

pyinstaller --noconfirm --clean --onefile --windowed --name PlaudIO ^
    --icon plaudio\plaudio.ico ^
    --add-data "plaudio\plaudio.ico;." ^
    --collect-submodules mcp ^
    --collect-data googleapiclient ^
    --hidden-import googleapiclient.discovery ^
    --hidden-import google_auth_oauthlib.flow ^
    packaging\plaudio_gui.py || goto :error

echo.
echo Built dist\PlaudIO.exe
exit /b 0

:error
echo.
echo Build failed.
exit /b 1
