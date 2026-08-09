param([Parameter(Mandatory=$true)][string]$Port,[ValidateSet('configuration_only','application_factory')][string]$Scope='configuration_only',[switch]$Confirm)
$ErrorActionPreference='Stop';$Root=Split-Path -Parent (Split-Path -Parent $PSScriptRoot);$Args=@('-m','sensor_package.tools.bootstrap.cli','factory-reset','--port',$Port,'--scope',$Scope);if($Confirm){$typed=Read-Host "Type the exact scope '$Scope' to confirm";if($typed -ne $Scope){throw 'Confirmation did not match'};$Args+='--confirm'}
Push-Location $Root;try{& python @Args;exit $LASTEXITCODE}finally{Pop-Location}
