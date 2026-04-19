@echo off
REM ============================================================
REM  Ghost — Windows build pipeline
REM    1. make_icons.py    (regenerate icon.ico from SVG)
REM    2. pyinstaller      (bundle app into dist\Ghost)
REM    3. Inno Setup       (produce installer\windows\Output\GhostSetup-<ver>.exe)
REM
REM  Reads version from src\version.py (single source of truth) and
REM  passes it to Inno Setup via /DAppVersion so the output filename
REM  and installer metadata stay in sync.
REM ============================================================
setlocal enabledelayedexpansion
cd /d "%~dp0\.."

if not exist ".venv\Scripts\python.exe" (
    echo [build] venv missing. Run run.bat once to provision it.
    exit /b 1
)

set "PY=.venv\Scripts\python.exe"
set "PYI=.venv\Scripts\pyinstaller.exe"
set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=C:\Program Files\Inno Setup 6\ISCC.exe"

REM Pull version from src/version.py
for /f "delims=" %%v in ('%PY% -c "import re;t=open('src/version.py').read();print(re.search(r'__version__\s*=\s*\"([^\"]+)\"', t).group(1))"') do set "VERSION=%%v"
if "%VERSION%"=="" (
    echo [build] could not parse version from src\version.py
    exit /b 1
)
echo [build] Ghost v%VERSION%

echo [build] 1/3 regenerating icons...
"%PY%" scripts\make_icons.py || goto :err

echo [build] 2/3 running PyInstaller...
"%PYI%" --noconfirm --clean ghost.spec || goto :err

echo [build] 3/3 compiling installer with Inno Setup...
if not exist "%ISCC%" (
    echo [build] ISCC.exe not found. Install Inno Setup 6: winget install -e --id JRSoftware.InnoSetup
    exit /b 1
)
"%ISCC%" /DAppVersion=%VERSION% installer\windows\ghost.iss || goto :err

echo [build] done. Installer: installer\windows\Output\GhostSetup-%VERSION%.exe
exit /b 0

:err
echo [build] FAILED
exit /b 1
