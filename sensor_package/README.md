# XIAO MG24 sensor package 0.1.0

This independently versioned product owns the `SiliconLabs:silabs:xiao_mg24` firmware, BLE protocol implementation, acquisition, filtering, alarm hooks, heartbeats, buffering, configuration validation, identity, build, flash, and release packaging. It has no FastAPI, SQLAlchemy, SQLite, dashboard, or Raspberry Pi service dependency.

The reproducible toolchain declaration is `toolchain.json`: Arduino CLI 1.5.1, the official Silicon Labs index, `SiliconLabs:silabs@4.0.0`, XIAO MG24 with `protocol_stack=ble_silabs`, and `Seeed Arduino LSM6DS3@2.0.7`. Run `./sensor_package/scripts/setup_arduino_cli.ps1` after installing Arduino CLI. `SilabsMicrophoneAnalog` is provided by the pinned core rather than installed separately.

Production firmware is flashed initially with an unassigned runtime identity. Assign and verify the permanent `node_id` afterward through USB using `scripts/provision_node.ps1`; the identity is stored in redundant NVM3 records. The optional ignored local header remains useful only for legacy builds and does not override a valid persistent identity.

PowerShell: `./sensor_package/scripts/compile.ps1`, then—only after reviewing the physical-write checkpoint—`./sensor_package/scripts/flash.ps1 -Port COM3`. Linux equivalents use `compile.sh` and `flash.sh`. The FQBN includes `protocol_stack=ble_silabs`; neither wrapper performs an erase or provisions identity.

Read-only state: `./sensor_package/scripts/read_node.ps1 -Port COM3`. Initial identity: `provision_node.ps1 -Port COM3 -NodeId MG24-0001`. Use `backup_node.ps1`, `restore_node.ps1`, and `verify_node.ps1` for application state. List attached boards with `list_nodes.ps1`. Factory reset requires an explicit port and immutable hardware ID; `factory_reset.ps1 -Port COM3 -HardwareId 0x0123456789ABCDEF -Confirm` then requires typing that exact hardware ID and uses a short-lived hardware-bound challenge. Backups belong under ignored `test_output/` or another protected deployment directory. See [`../docs/sensor-lifecycle.md`](../docs/sensor-lifecycle.md) for reset recovery and preserved data.

Install the independent host-tool dependency with `python -m pip install -r sensor_package/requirements.txt` (`pyserial==3.5`).

Persistence uses the core's default NVM3 instance with explicit high user-domain keys, redundant slots, bounded 254-byte objects, and CRC32. Unknown calibration and alarms remain disabled and raw inputs remain `adc_count`. See `docs/device-identity.md` and `reset_scope.json` for exact ownership and deletion scope.

Run `python sensor_package/scripts/package_release.py sensor_package/build/<artifact>` only after compilation. The package contains the artifact, checksum, manifest, and release notes; it does not claim reproducibility because core/library versions are not yet pinned exactly. OTA is not implemented. Verify identity, versions, capabilities, BLE service, telemetry, and restored configuration after every deliberate USB update.

External sensor calibration, electrical design, and alarm limits remain disabled until authoritative hardware information is supplied. Built-in profile data is declarative under `profiles/built_in`; shared message schemas live only in `../shared_protocol`.
