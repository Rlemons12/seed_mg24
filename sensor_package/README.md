# XIAO MG24 sensor package 0.1.0

This independently versioned product owns the `SiliconLabs:silabs:xiao_mg24` firmware, BLE protocol implementation, acquisition, filtering, alarm hooks, heartbeats, buffering, configuration validation, identity, build, flash, and release packaging. It has no FastAPI, SQLAlchemy, SQLite, dashboard, or Raspberry Pi service dependency.

Copy `config/device_config.local.h.example` to the ignored `config/device_config.local.h`, assign a unique backed-up `DEVICE_ID`, and record the assignment before flashing. Duplicate IDs must be prevented in the deployment inventory. Restore the same ID to a replacement only after retiring the prior board.

PowerShell: `./sensor_package/scripts/compile.ps1`, then `./sensor_package/scripts/flash.ps1 -Port COM3`. Linux: `./sensor_package/scripts/compile.sh`, then `./sensor_package/scripts/flash.sh /dev/ttyACM0`. Flash wrappers refuse a production flash without local identity configuration. The FQBN includes `protocol_stack=ble_silabs`.

Run `python sensor_package/scripts/package_release.py sensor_package/build/<artifact>` only after compilation. The package contains the artifact, checksum, manifest, and release notes; it does not claim reproducibility because core/library versions are not yet pinned exactly. OTA is not implemented. Verify identity, versions, capabilities, BLE service, telemetry, and restored configuration after every deliberate USB update.

External sensor calibration, electrical design, and alarm limits remain disabled until authoritative hardware information is supplied. Built-in profile data is declarative under `profiles/built_in`; shared message schemas live only in `../shared_protocol`.
