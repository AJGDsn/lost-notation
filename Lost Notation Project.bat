@echo off
setlocal EnableExtensions
title Lost Notation Project

set "PROJECT_DIR=%~dp0"
cd /d "%PROJECT_DIR%"
set "PYTHONPATH=%PROJECT_DIR%src"
set "DATA_DIR=C:\LostNotationData"
if not exist "%DATA_DIR%" mkdir "%DATA_DIR%"

where py >nul 2>nul
if not errorlevel 1 (
    set "PYTHON=py -3"
) else (
    where python >nul 2>nul
    if not errorlevel 1 (
        set "PYTHON=python"
    ) else (
        echo.
        echo Python 3 was not found on this computer.
        echo Install Python from https://www.python.org/downloads/ and select Add Python to PATH.
        echo.
        pause
        exit /b 1
    )
)

echo =============================================
echo              Lost Notation Project
echo =============================================
echo.
echo Opening the research dashboard in your browser...
echo Keep this window open while using the project.
echo Press Ctrl+C here when you have finished.
echo.

%PYTHON% -m lost_notation.cli --data-dir "%DATA_DIR%" dashboard --open
if errorlevel 1 (
    echo.
    echo The dashboard stopped unexpectedly. The message above may explain why.
    pause
)
