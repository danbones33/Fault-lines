@echo off
rem FAULT LINES for Windows: double-click this file. The first time it installs what it needs (a few minutes),
rem then it opens the app in your browser. Keep this window open while you use the app; close it to stop.
setlocal
cd /d "%~dp0"
title FAULT LINES
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install.ps1"
if errorlevel 1 (
  echo.
  echo Setup did not finish. Read the message above, then double-click start.cmd again.
  pause
  exit /b 1
)
set "PATH=%USERPROFILE%\.local\bin;%PATH%"
uv run run.py
echo.
echo FAULT LINES has stopped.
pause
