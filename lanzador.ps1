# lanzador.ps1 — Wrapper de Windows para lanzador.py (agente genealógico v9.0).
#
# Uso:
#   - Doble clic en este archivo (PowerShell ejecuta lanzador.py y la
#     ventana se queda abierta con el menú).
#   - Desde una terminal:  .\lanzador.ps1            (equivale a lanzador.py)
#                          .\lanzador.ps1 --sin-chequeo
#
# Este archivo NO contiene lógica: solo localiza Python, fuerza UTF-8 en la
# consola y llama a lanzador.py (que es multiplataforma y es quien hace todo).

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
$lanzador = Join-Path $dir "lanzador.py"

if (-not (Test-Path $lanzador)) {
    Write-Host "[x] No encuentro lanzador.py junto a este script ($dir)."
    Read-Host "Pulsa Enter para cerrar"
    exit 1
}

# Localizar Python: python > py (lanzador de Windows).
$exe = $null
if (Get-Command python -ErrorAction SilentlyContinue) {
    $exe = "python"
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    $exe = "py"
} else {
    Write-Host "[x] Python no encontrado en el PATH."
    Write-Host "    Instálalo desde https://www.python.org/downloads/ "
    Write-Host "    marcando 'Add python.exe to PATH' en el instalador."
    Read-Host "Pulsa Enter para cerrar"
    exit 1
}

# Ejecutar el menú pasándole los argumentos extra (p. ej. --sin-chequeo).
& $exe $lanzador @args
$codigo = $LASTEXITCODE

# Si algo falló, pausamos para que el doble clic no cierre la ventana
# sin dejar ver el error.
if ($codigo -ne 0) {
    Write-Host ""
    Write-Host "[x] lanzador.py terminó con código $codigo."
    Read-Host "Pulsa Enter para cerrar"
}
exit $codigo
