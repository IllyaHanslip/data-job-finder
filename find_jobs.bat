@echo off
rem Double-click this file to run the job search with the project's virtual environment.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo The virtual environment is missing. Follow the Setup steps in README.md first.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" find_jobs.py %*
pause
