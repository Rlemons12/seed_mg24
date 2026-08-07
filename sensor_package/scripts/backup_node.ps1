param([Parameter(Mandatory=$true)][string]$Port,[Parameter(Mandatory=$true)][string]$OutputFile,[switch]$IncludeIdentity)
$ErrorActionPreference='Stop'; $Root=Split-Path -Parent (Split-Path -Parent $PSScriptRoot); $Args=@('-m','sensor_package.tools.bootstrap.cli','backup','--port',$Port,'--file',$OutputFile);if($IncludeIdentity){$Args+='--include-identity'}
Push-Location $Root; try { & python @Args; exit $LASTEXITCODE } finally { Pop-Location }
