param([Parameter(Mandatory=$true)][string]$Port,[Parameter(Mandatory=$true)][string]$NodeId)
$ErrorActionPreference='Stop'; $Root=Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Push-Location $Root; try { python -m sensor_package.tools.bootstrap.cli provision --port $Port --node-id $NodeId; exit $LASTEXITCODE } finally { Pop-Location }
