# Sensor package changelog

## 0.1.4

- Add an NVM3-backed circular telemetry-summary journal for gateway outages.
- Batch four one-minute summaries per flash object and retain up to 32 summaries.
- Replay oldest-first and erase a batch only after SQLite persistence acknowledgement.
- Preserve replay identity across sensor restarts without fabricating unknown wall-clock measurement time.

## 0.1.3

- Add a runtime `LOW_POWER` mode using EM2-capable one-second sleep slices.
- Power down the IMU and battery-divider rails between five-minute snapshots.
- Pause vibration windows in low-power mode and safely return to Edge Summary after BLE disconnect or reboot.

## 0.1.2

- Power-cycle only the IMU rail between bounded initialization retries.
- Preserve `IMU STATUS` commands in the USB bootstrap resynchronization parser.
- Mark accelerometer and gyroscope readings as `sensor_fault` when the IMU is unavailable.

## 0.1.1

- Retry IMU and vibration-service initialization after transient startup failures, with bounded attempt counts and `WHO_AM_I` diagnostics.
- Drive Live telemetry from its configured report interval when microphone hardware is disabled.
- Keep microphone hardware excluded and Edge Summary as the boot default.

## 0.1.0

- Package the MG24 firmware, local processing, configuration, build, flash, and release assets independently.
