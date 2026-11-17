@echo off
rem Doble clic para cambiar la clave de Gemini.
chcp 65001 >nul
cd /d "%~dp0"
.venv\Scripts\python.exe -m mail_crm.clave_gemini
echo.
pause
