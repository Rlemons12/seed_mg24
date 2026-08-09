param([ValidateRange(1,30)][int]$Timeout=3)
$ErrorActionPreference='Stop'; $Root=Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Push-Location $Root; try { python -m sensor_package.tools.bootstrap.cli list --timeout $Timeout; exit $LASTEXITCODE } finally { Pop-Location }
