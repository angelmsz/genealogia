@echo off
rem ===========================================================================
rem  Menu.bat — arranque del menú de tareas con DOBLE CLIC (v10.4.1)
rem
rem  No contiene lógica: solo prepara el entorno y llama a menu_principal.py.
rem  Usa el Python del .venv del proyecto si existe (es el que tiene las
rem  dependencias instaladas); si no, el 'python' del PATH.
rem ===========================================================================
cd /d "%~dp0"

rem UTF-8 en la consola: los iconos de la salida del bot no deben romperla.
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" menu_principal.py %*
) else (
    python menu_principal.py %*
)

pause
