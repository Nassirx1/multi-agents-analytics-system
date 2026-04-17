@echo off
setlocal

cd /d "%~dp0"
title Multi-Agent Analytics Workflow

echo.
echo Multi-Agent Analytics Workflow
echo ==============================
echo Workspace: %CD%
echo.

if exist ".venv\Scripts\python.exe" (
    echo Using project virtual environment.
    echo.
    ".venv\Scripts\python.exe" -m analytics_workflow
    goto :finish
)

where py >nul 2>nul
if %errorlevel%==0 (
    echo Using system launcher: py
    echo.
    py -m analytics_workflow
    goto :finish
)

where python >nul 2>nul
if %errorlevel%==0 (
    echo Using system launcher: python
    echo.
    python -m analytics_workflow
    goto :finish
)

echo Python was not found.
echo Create .venv or install Python and try again.

:finish
echo.
pause
