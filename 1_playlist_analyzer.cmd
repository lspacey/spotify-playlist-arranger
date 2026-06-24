@echo off
chcp 65001 >nul

REM Read .env and set vars in the current shell
for /f "usebackq tokens=1,* delims==" %%A in (".env") do (
    REM Skip comments and empty lines
    if not "%%A"=="" (
        set "%%A=%%B"
    )
)

echo Env variables are loaded
echo Starting...

python playlist_analyzer.py
pause