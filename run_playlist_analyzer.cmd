@echo off
cd /d "%~dp0"
chcp 65001 >nul

REM Activate virtual environment
if exist "venv\Scripts\activate.bat" call venv\Scripts\activate.bat
if exist ".venv\Scripts\activate.bat" call .venv\Scripts\activate.bat

REM Load .env variables into current shell
echo Loading environment variables from .env ...
for /f "usebackq tokens=1,* delims==" %%A in (".env") do (
    set "line=%%A"
    if not "%%A"=="" (
        if not "!line:~0,1!"=="#" (
            set "%%A=%%B"
        )
    )
)

echo.
echo All environment variables are loaded.
echo Starting Spotify Playlist Analyzer...
echo.

python playlist_analyzer.py
pause
