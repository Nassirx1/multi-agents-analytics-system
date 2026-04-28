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
    ".venv\Scripts\python.exe" --version >nul 2>nul
    if errorlevel 1 goto :skip_venv
    echo Using project virtual environment.
    echo.
    ".venv\Scripts\python.exe" -m analytics_workflow
    goto :finish
)

:skip_venv
if exist "%LocalAppData%\Programs\Python\Launcher\py.exe" (
    echo Using system launcher: %LocalAppData%\Programs\Python\Launcher\py.exe
    echo.
    "%LocalAppData%\Programs\Python\Launcher\py.exe" -m analytics_workflow
    goto :finish
)

where py >nul 2>nul
if %errorlevel%==0 (
    echo Using system launcher: py
    echo.
    py -m analytics_workflow
    goto :finish
)

if exist "%LocalAppData%\Programs\Python\Python312\python.exe" (
    echo Using system Python: %LocalAppData%\Programs\Python\Python312\python.exe
    echo.
    "%LocalAppData%\Programs\Python\Python312\python.exe" -m analytics_workflow
    goto :finish
)

if exist "%LocalAppData%\Programs\Python\Python311\python.exe" (
    echo Using system Python: %LocalAppData%\Programs\Python\Python311\python.exe
    echo.
    "%LocalAppData%\Programs\Python\Python311\python.exe" -m analytics_workflow
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
