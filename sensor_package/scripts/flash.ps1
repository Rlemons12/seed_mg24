param([Parameter(Mandatory=$true)][string]$Port,[string]$BuildIdentifier='local')
$ErrorActionPreference='Stop'; $Package=Split-Path -Parent $PSScriptRoot
& (Join-Path $PSScriptRoot 'compile.ps1') -BuildIdentifier $BuildIdentifier
if($LASTEXITCODE -ne 0){exit $LASTEXITCODE}
& arduino-cli upload -p $Port --fqbn 'SiliconLabs:silabs:xiao_mg24:protocol_stack=ble_silabs' --input-dir (Join-Path $Package 'build') (Join-Path $Package 'firmware/xiao_mg24_sensor_node')
if($LASTEXITCODE -ne 0){exit $LASTEXITCODE}
