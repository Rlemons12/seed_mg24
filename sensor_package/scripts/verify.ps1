param([string]$Port)
$ErrorActionPreference='Stop'; $Package=Split-Path -Parent $PSScriptRoot; $Root=Split-Path -Parent $Package
python (Join-Path $Root 'scripts/verify_compatibility.py')
if($LASTEXITCODE -ne 0){exit $LASTEXITCODE}
if($Port){Write-Host "Open serial monitor and confirm identity/version output: arduino-cli monitor -p $Port"}
