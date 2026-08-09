param([Parameter(Mandatory=$true)][string]$Port)
$ErrorActionPreference='Stop'; $Root=Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Push-Location $Root; try { python -m sensor_package.tools.bootstrap.cli read --port $Port; exit $LASTEXITCODE } finally { Pop-Location }
