@echo off
setlocal EnableExtensions
title Lost Notation Project - iPad access

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

echo =============================================================
echo              Lost Notation Project - iPad mode
echo =============================================================
echo.
echo This keeps the archive on this PC and lets an iPad use it over
echo your private home network. The normal launcher still works too.
echo.
echo If Windows asks about network access, select PRIVATE networks
echo only, then choose Allow access.
echo.

%PYTHON% -m lost_notation.cli --data-dir "%DATA_DIR%" dashboard --lan --open
if errorlevel 1 (
    echo.
    echo The dashboard stopped unexpectedly. Make sure the normal
    echo Lost Notation launcher is not already running, then try again.
    pause
)
