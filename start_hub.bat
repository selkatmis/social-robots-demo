@echo off
cd /d "%~dp0"
if exist "venv\Scripts\python.exe" (
    "venv\Scripts\python.exe" -m hub
) else (
    python -m hub
)
if errorlevel 1 pause
