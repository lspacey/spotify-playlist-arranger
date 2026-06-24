@echo off
chcp 65001 >nul

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