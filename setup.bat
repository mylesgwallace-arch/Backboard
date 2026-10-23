@echo off
rem Backboard: set up (first run) or update and start (later runs).
rem Same as: powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1 %*
rem Options: -NoUpdate -NoSchedule -NoStart -TestData -Port 8000
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup_windows.ps1" %*
if errorlevel 1 pause
