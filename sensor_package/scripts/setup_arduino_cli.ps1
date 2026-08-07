$ErrorActionPreference='Stop'
if(-not(Get-Command arduino-cli -ErrorAction SilentlyContinue)){throw 'Install Arduino CLI from https://arduino.github.io/arduino-cli/'}
arduino-cli core update-index
arduino-cli core install SiliconLabs:silabs
arduino-cli lib install LSM6DS3
