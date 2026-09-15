# lanzador.ps1 — Arranque de Windows para el MENÚ de tareas (menu_principal.py).
#
# HISTORIA: este wrapper apuntaba al antiguo `lanzador.py`, el menú en proceso
# que se retiró en la v10.4.2 (el menú bueno es `menu_principal.py`, que lanza
# los comandos como subprocesos). Se conserva el nombre del fichero para no
# romper los accesos directos de doble clic que ya existan, pero AHORA ABRE EL
# MENÚ NUEVO. En Windows también está `Menu.bat`, que hace lo mismo.
#
# Uso:
#   - Doble clic en este archivo (se abre el menú; la ventana se queda abierta).
#   - Desde una terminal:  .\lanzador.ps1
#
# Este archivo NO contiene lógica: solo localiza Python (el .venv del proyecto
# si existe), fuerza UTF-8 en la consola y llama a menu_principal.py.

$ErrorActionPreference = "Stop"

# UTF-8 en la consola para que los iconos del menú se vean bien.
try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    $OutputEncoding = [System.Text.Encoding]::UTF8
} catch {
    # consola antigua: seguimos sin UTF-8, no es fatal
}

# Carpeta de este script (independiente de desde dónde se ejecute).
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$menu = Join-Path $dir "menu_principal.py"

if (-not (Test-Path $menu)) {
    Write-Host "[x] No encuentro menu_principal.py junto a este script ($dir)."
    Read-Host "Pulsa Enter para cerrar"
    exit 1
}

# Intérprete: el .venv del proyecto (el que tiene las dependencias) y, si no
# existe, el python del PATH.
$exe = $null
$venv = Join-Path $dir ".venv\Scripts\python.exe"
if (Test-Path $venv) {
    $exe = $venv
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $exe = "python"
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    $exe = "py"
} else {
    Write-Host "[x] Python no encontrado (ni el .venv del proyecto ni el PATH)."
    Write-Host "    Instálalo desde https://www.python.org/downloads/ "
    Write-Host "    marcando 'Add python.exe to PATH' en el instalador."
    Read-Host "Pulsa Enter para cerrar"
    exit 1
}

# Ejecutar el menú pasándole los argumentos extra (p. ej. --sin-pausa).
& $exe $menu @args
$codigo = $LASTEXITCODE

# Si algo falló, pausamos para que el doble clic no cierre la ventana
# sin dejar ver el error.
if ($codigo -ne 0) {
    Write-Host ""
    Write-Host "[x] menu_principal.py terminó con código $codigo."
    Read-Host "Pulsa Enter para cerrar"
}
exit $codigo
