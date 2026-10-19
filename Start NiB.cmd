@echo off
rem Double-click entry point for Windows.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0bin\windows\run.ps1" %*
