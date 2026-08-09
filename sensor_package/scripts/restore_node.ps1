param([Parameter(Mandatory=$true)][string]$Port,[Parameter(Mandatory=$true)][string]$BackupFile,[switch]$Confirm)
$ErrorActionPreference='Stop'; $Root=Split-Path -Parent (Split-Path -Parent $PSScriptRoot);$Args=@('-m','sensor_package.tools.bootstrap.cli','restore','--port',$Port,'--file',$BackupFile);if($Confirm){$Args+='--confirm'}
Push-Location $Root; try { & python @Args; exit $LASTEXITCODE } finally { Pop-Location }
