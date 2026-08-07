$ErrorActionPreference='Stop'
$Package=Split-Path -Parent $PSScriptRoot
$Toolchain=Get-Content (Join-Path $Package 'toolchain.json') -Raw | ConvertFrom-Json
$Cli=Get-Command arduino-cli -ErrorAction SilentlyContinue
if(-not $Cli){throw 'Install Arduino CLI from https://arduino.github.io/arduino-cli/'}
$actualVersion=((& arduino-cli version --format json | ConvertFrom-Json).VersionString -replace '^v','')
if($actualVersion -ne $Toolchain.arduino_cli.tested_version){throw "Arduino CLI $actualVersion is installed; expected tested version $($Toolchain.arduino_cli.tested_version)"}
$configText=& arduino-cli config dump
$url=$Toolchain.board_manager.package_url
$urlCount=($configText | Select-String -SimpleMatch $url).Count
if($urlCount -eq 0){& arduino-cli config add board_manager.additional_urls $url; if($LASTEXITCODE -ne 0){exit $LASTEXITCODE}}
elseif($urlCount -gt 1){throw "Arduino CLI configuration contains the Silicon Labs package URL more than once"}
& arduino-cli core update-index
if($LASTEXITCODE -ne 0){exit $LASTEXITCODE}
& arduino-cli core install "$($Toolchain.board_manager.core)@$($Toolchain.board_manager.core_version)"
if($LASTEXITCODE -ne 0){exit $LASTEXITCODE}
foreach($library in $Toolchain.libraries | Where-Object {$_.source -eq 'Arduino Library Manager'}) {
  & arduino-cli lib install "$($library.name)@$($library.version)"
  if($LASTEXITCODE -ne 0){exit $LASTEXITCODE}
}
& arduino-cli board details --fqbn "$($Toolchain.board.fqbn):protocol_stack=$($Toolchain.board.options.protocol_stack)" | Out-Null
if($LASTEXITCODE -ne 0){throw 'Pinned board FQBN or BLE protocol-stack option is unavailable'}
Write-Host "Arduino toolchain ready: CLI $actualVersion, core $($Toolchain.board_manager.core_version), $($Toolchain.board.fqbn):protocol_stack=$($Toolchain.board.options.protocol_stack)"
