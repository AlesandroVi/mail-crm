@echo off
rem Doble clic para conectar la app con tu base de Supabase y copiar tus datos.
chcp 65001 >nul
cd /d "%~dp0"
.venv\Scripts\python.exe -m mail_crm.conexion
if errorlevel 1 goto fin
echo.
echo Copiando tus datos a la nube...
echo.
.venv\Scripts\python.exe -m mail_crm.migrar
:fin
echo.
pause
