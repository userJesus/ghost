@echo off
cd /d "%~dp0"
if not exist ".venv" (
    echo Criando ambiente virtual com Python 3.12...
    py -3.12 -m venv .venv
    call .venv\Scripts\activate.bat
    pip install -r requirements.txt
) else (
    call .venv\Scripts\activate.bat
)
pythonw main.py
