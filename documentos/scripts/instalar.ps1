param(
    [switch]$ConPruebas,
    [switch]$SinMigraciones,
    [string]$Python = 'python'
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path

Push-Location $projectRoot
try {
    $venvPython = Join-Path $projectRoot 'venv/Scripts/python.exe'
    if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
        & $Python -m venv venv
        if ($LASTEXITCODE -ne 0) { throw 'No se pudo crear el entorno virtual.' }
    }

    $requirements = if ($ConPruebas) { 'requirements-dev.txt' } else { 'requirements.txt' }
    & $venvPython -m pip install -r $requirements
    if ($LASTEXITCODE -ne 0) { throw 'No se pudieron instalar las dependencias.' }

    $envPath = Join-Path $projectRoot '.env'
    if (-not (Test-Path -LiteralPath $envPath)) {
        $secret = & $venvPython -c 'import secrets; print(secrets.token_urlsafe(48))'
        if ($LASTEXITCODE -ne 0) { throw 'No se pudo generar la clave de sesion.' }
        $template = Get-Content -LiteralPath (Join-Path $projectRoot '.env.example') -Raw
        $content = $template -replace '(?m)^SECRET_KEY=.*$', ('SECRET_KEY=' + $secret.Trim())
        [System.IO.File]::WriteAllText($envPath, $content, [System.Text.UTF8Encoding]::new($false))
        Write-Host '.env creado con una clave de sesion nueva y la IA desactivada.'
    } else {
        Write-Host 'Se conserva el .env existente. No se muestran ni reemplazan sus valores.'
    }

    if (-not $SinMigraciones) {
        Write-Host 'Actualizando el esquema. En instalaciones existentes se requiere respaldo previo.'
        & $venvPython -m flask --app run db upgrade
        if ($LASTEXITCODE -ne 0) { throw 'No se pudo actualizar la base de datos.' }
    }
    Write-Host 'Preparacion terminada. Para iniciar: .\documentos\scripts\iniciar.ps1'
} finally {
    Pop-Location
}
