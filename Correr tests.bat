@echo off
rem Doble clic para comprobar que todo sigue funcionando. No toca tu Gmail ni tu base.
chcp 65001 >nul
cd /d "%~dp0"
.venv\Scripts\python.exe -m pytest
echo.
pause
