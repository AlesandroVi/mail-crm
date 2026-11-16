@echo off
rem Doble clic para definir o cambiar la contrasena del panel.
chcp 65001 >nul
cd /d "%~dp0"
.venv\Scripts\python.exe -m mail_crm.clave
echo.
pause
