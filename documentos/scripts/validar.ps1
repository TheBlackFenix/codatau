$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$venvPython = Join-Path $projectRoot 'venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
    throw 'Falta el entorno virtual. Instala con instalar.ps1 -ConPruebas.'
}

# Keep fixture paths short: Windows may reject long analytical temp filenames.
$runDirectory = Join-Path $projectRoot ('output/v-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Path $runDirectory | Out-Null
$overrides = @{
    DATABASE_URL = 'sqlite:///' + (Join-Path $runDirectory 'validacion.db').Replace('\', '/')
    UPLOAD_FOLDER = Join-Path $runDirectory 'uploads'
    ANALYTICS_FOLDER = Join-Path $runDirectory 'artifacts'
    AI_PROVIDER = 'disabled'
    AI_MODEL_SELECTOR_ENABLED = '0'
    RUN_LIVE_AI_TEST = '0'
}
$previous = @{}
Push-Location $projectRoot
try {
    foreach ($name in $overrides.Keys) {
        $previous[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
        [Environment]::SetEnvironmentVariable($name, $overrides[$name], 'Process')
    }
    & $venvPython -m compileall -q app run.py documentos/scripts
    if ($LASTEXITCODE -ne 0) { throw 'Fallo la comprobacion de sintaxis de Python.' }
    & $venvPython -m flask --app run db upgrade
    if ($LASTEXITCODE -ne 0) { throw 'Fallo la creacion de la base de prueba.' }
    & $venvPython -m flask --app run db check
    if ($LASTEXITCODE -ne 0) { throw 'Los modelos y las migraciones no coinciden.' }
    & $venvPython -m pytest -q -p no:cacheprovider "--basetemp=$(Join-Path $runDirectory 'p')" --tb=short
    if ($LASTEXITCODE -ne 0) { throw 'Hay pruebas Python fallidas.' }
    if (Get-Command node -ErrorAction SilentlyContinue) {
        & node --test tests/chat_presentation.test.cjs
        if ($LASTEXITCODE -ne 0) { throw 'Hay pruebas JavaScript fallidas.' }
    } else {
        Write-Warning 'Node.js no esta disponible; no se ejecutaron las pruebas JavaScript.'
    }
    Write-Host "Validacion terminada. Evidencia temporal en: $runDirectory"
    Write-Host 'No se usaron la base de trabajo ni la API de IA.'
} finally {
    foreach ($name in $previous.Keys) {
        [Environment]::SetEnvironmentVariable($name, $previous[$name], 'Process')
    }
    Pop-Location
}
