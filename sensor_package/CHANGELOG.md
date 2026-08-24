# Sensor package changelog

## 0.1.1

- Retry IMU and vibration-service initialization after transient startup failures, with bounded attempt counts and `WHO_AM_I` diagnostics.
- Drive Live telemetry from its configured report interval when microphone hardware is disabled.
- Keep microphone hardware excluded and Edge Summary as the boot default.

## 0.1.0

- Package the MG24 firmware, local processing, configuration, build, flash, and release assets independently.
