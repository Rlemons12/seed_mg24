param([string]$BuildIdentifier="local", [switch]$Production)
$ErrorActionPreference='Stop'
$Package=Split-Path -Parent $PSScriptRoot
$Root=Split-Path -Parent $Package
$Sketch=Join-Path $Package 'firmware/xiao_mg24_sensor_node'
$Build=Join-Path $Package 'build'
$Config=Join-Path $Package 'config/device_config.local.h'
if(-not(Get-Command arduino-cli -ErrorAction SilentlyContinue)){throw 'arduino-cli is required'}
if($Production -and -not(Test-Path $Config)){throw 'Production build requires config/device_config.local.h'}
$NodeId='UNASSIGNED-MG24'
if(Test-Path $Config){$match=Select-String -Path $Config -Pattern '#define\s+DEVICE_ID\s+"([A-Z0-9-]+)"'; if(-not $match){throw 'Valid DEVICE_ID is required'}; $NodeId=$match.Matches[0].Groups[1].Value}
if($Production -and $NodeId -eq 'UNASSIGNED-MG24'){throw 'Production identity is unassigned'}
$SensorVersion=(Get-Content (Join-Path $Package 'VERSION') -Raw).Trim(); $ProtocolVersion=(Get-Content (Join-Path $Root 'shared_protocol/VERSION') -Raw).Trim()
$GitCommit=(git -C $Root rev-parse --short=12 HEAD 2>$null); if(-not $GitCommit){$GitCommit='unknown'}; if(git -C $Root status --porcelain){$GitCommit="$GitCommit-dirty"}
$Flags="-I`"$(Join-Path $Package 'config')`" -DDEVICE_ID=\`"$NodeId\`" -DSENSOR_PACKAGE_VERSION=\`"$SensorVersion\`" -DFIRMWARE_VERSION=\`"$SensorVersion\`" -DPROTOCOL_VERSION=\`"$ProtocolVersion\`" -DBUILD_IDENTIFIER=\`"$BuildIdentifier\`" -DFIRMWARE_GIT_COMMIT=\`"$GitCommit\`""
New-Item -ItemType Directory -Force $Build | Out-Null
Write-Host "Building sensor $SensorVersion, protocol $ProtocolVersion, node $NodeId"
& arduino-cli compile --fqbn 'SiliconLabs:silabs:xiao_mg24:protocol_stack=ble_silabs' --build-path $Build --build-property "compiler.cpp.extra_flags=$Flags" $Sketch
if($LASTEXITCODE -ne 0){exit $LASTEXITCODE}
