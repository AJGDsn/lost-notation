@echo off
setlocal EnableExtensions
title Lost Notation Project - Archive Survey

set "PROJECT_DIR=%~dp0"
cd /d "%PROJECT_DIR%"
set "PYTHONPATH=%PROJECT_DIR%src"
set "DATA_DIR=C:\LostNotationData"
if not exist "%DATA_DIR%" mkdir "%DATA_DIR%"
set "SURVEY_LOG=%DATA_DIR%\latest-survey.log"
set "SURVEY_PAGES=120"

where py >nul 2>nul
if not errorlevel 1 (
    set "PYTHON=py -3"
) else (
    set "PYTHON=python"
)

echo Lost Notation Project survey log > "%SURVEY_LOG%"
echo Started: %DATE% %TIME%>> "%SURVEY_LOG%"
echo Making the next %SURVEY_PAGES% archive index requests, most promising sections first.
echo This takes roughly one to two hours and downloads no pages.
echo Progress is saved after every page: you can close this window at any time
echo and run this file again later to continue where it stopped.
echo.
call %PYTHON% -m lost_notation.cli --data-dir "%DATA_DIR%" discover-ia-seeds --input "%PROJECT_DIR%examples\wayback-seeds.jsonl" --pages %SURVEY_PAGES% --dry-run --log "%SURVEY_LOG%" 2>> "%SURVEY_LOG%"
set "SURVEY_EXIT=%ERRORLEVEL%"

echo.
if not "%SURVEY_EXIT%"=="0" (
    echo The survey stopped unexpectedly. The full diagnostic follows and is saved in %SURVEY_LOG%
    echo.
    type "%SURVEY_LOG%"
) else (
    echo Finished. Any matching sites are waiting in Review on the dashboard.
)
pause
