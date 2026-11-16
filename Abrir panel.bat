@echo off
rem Doble clic para abrir el panel de Mail CRM.
cd /d "%~dp0"
rem Arranca el servidor en una ventana minimizada (si ya estaba abierto, esa ventana se cierra sola).
start "Mail CRM - servidor (cerrar para apagar el panel)" /min cmd /c ".venv\Scripts\python.exe -m mail_crm.panel"
timeout /t 3 /nobreak >nul
start "" http://127.0.0.1:8000
