# Raspberry Pi gateway 0.1.0

The dashboard uses a reusable, responsive module shell for overview, devices,
installations, firmware, and system-health surfaces. See
[`docs/frontend-module-template.md`](docs/frontend-module-template.md) before
adding a new page or navigation item.

This component owns BLE discovery and connections, the node and attached-sensor registry, declarative profile import, provisioning, SQLite history, FastAPI, WebSockets, and the browser dashboard. It contains no Arduino source or firmware build logic.

Install with `python -m pip install -r gateway/requirements.txt`, copy `.env.example` to `.env`, and run `python -m gateway`. The dashboard listens on the configured host/port (default `0.0.0.0:8000`). Use `sudo bash gateway/scripts/install_raspberry_pi.sh` for the systemd installation. The service needs membership in the `bluetooth` group and access to BlueZ D-Bus.

Back up the configured SQLite file and `data/sensor_profiles/` together while the service is stopped. Sensor onboarding preserves separate node, installation, equipment, interface, and channel identities. Firmware compatibility is shown from metadata read over BLE; incompatible or missing metadata does not count as safe configuration compatibility.

The normal dashboard workflow treats the physical MG24 as a device first. **Add Sensor** scans USB and BLE, then offers exactly one safe action: install approved application firmware for a supported USB board, set up an authoritatively unassigned node, open a locally registered node, or import an assigned node from another database. Import is read-only on the sensor: it reads identity, metadata, capabilities, and persistent configuration, creates one local node record, and creates no installation record.

Device configuration is separate from optional installation/profile metadata. The current firmware persists one device-level processing record: microphone sample/process/filter settings plus the device reporting and heartbeat cadence. IMU, battery, and analog channels remain visible as live telemetry but do not claim separate persistent controls. Apply uses a bounded `CFGSET` transaction, correlated acknowledgement, and authoritative `PROVGET` readback; telemetry owns the BLE connection again afterward.

Only one gateway process may own a repository port at a time. Startup fails clearly when another instance already holds the process lock, preventing competing telemetry and configuration connections.

The Add Sensor dialog supports preflashed unassigned nodes and blank supported USB boards. An unassigned discovery uses an expiring transport identity, then becomes a permanent database record only after write-once BLE provisioning and readback. Firmware endpoints are loopback-only and reject arbitrary paths, arbitrary uploader arguments, bootloader images, hash mismatches, wrong boards, and protected-region overlap.

Application-only firmware installation preserves NVM3 identity and configuration. After installation, BLE assignment-state readback decides the next action: a node already in this database offers **Reconnect / View Sensor**; an assigned node absent from this database requires restoration/import of its original gateway database or the explicit USB application-factory recovery workflow. Ordinary Add Sensor never replaces identity, and deterministic assignment conflicts are displayed without automatic retry.

The Devices screen provides separate **Remove**, **Restore/Reapprove**, and **Factory Reset** actions. Removal archives gateway membership and installations while preserving telemetry and the physical sensor. Removed devices cannot reactivate through heartbeat, reconnect, import, or ordinary onboarding. Restore reuses the archived record after identity matching. Factory reset requires a loopback USB connection and verified immutable MCU hardware ID; it reports success only after reboot read-back and gateway cleanup. See [`../docs/sensor-lifecycle.md`](../docs/sensor-lifecycle.md).
