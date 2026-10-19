param([ValidateRange(1024, 65535)][int]$Puerto = 5000)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$venvPython = Join-Path $projectRoot 'venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
    throw 'Falta el entorno virtual. Ejecuta primero documentos/scripts/instalar.ps1.'
}

Push-Location $projectRoot
try {
    & $venvPython -c "from app.server import ensure_port_available; ensure_port_available('127.0.0.1', $Puerto)"
    if ($LASTEXITCODE -ne 0) { throw 'El puerto esta ocupado. No se detuvo ningun proceso existente.' }
    Write-Host "CoDataU local: http://127.0.0.1:$Puerto (Ctrl+C para detener). No se abre ningun tunel."
    & $venvPython -m flask --app run --no-debug run --host 127.0.0.1 --port $Puerto --no-debugger --no-reload
    if ($LASTEXITCODE -ne 0) { throw 'El servidor termino con un error.' }
} finally {
    Pop-Location
}
