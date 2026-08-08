# Shared protocol 1.0.0

This directory is the authoritative data contract between the MG24 sensor package and the Raspberry Pi gateway. JSON Schemas define metadata, telemetry, capabilities, commands, and configuration. Fixtures must validate against the matching schema.

Backward-compatible optional fields may be added within protocol 1.x. Removing fields, changing meanings or units, tightening accepted values, or changing framing requires a new major version and compatibility-matrix update. Database and UI concepts do not belong here.

BLE onboarding reuses the command characteristic. `PROV 1` carries a bounded transaction ID, permanent node ID, and supported timing/filter fields. Firmware validates the whole request, verifies the redundant configuration write, and commits write-once identity last. `PROVGET 1` returns correlated readback for idempotent recovery. A matching assigned identity may be read back; replacement identity, reset, and recovery remain USB-only.

Assigned devices use `CFGSET 1 <transaction-id> <sample> <process> <report> <heartbeat> <filter> <window> <enabled>` for the single device-level persistent processing record. `CFGSET` cannot assign or replace identity. The gateway reads with `PROVGET` before writing (so a repeated request whose values already match performs no write), requires a correlated acknowledgement, reads again, and reports success only when every persisted value matches.

USB bootstrap protocol v1 uses newline-delimited `MG24BOOT1 ` framing followed by bounded JSON. Requests and responses carry a schema version, bounded request ID, action, and correlated result/error. Canonical schemas cover bootstrap requests, responses, and node backups. No schema permits arbitrary NVM keys or broad erase operations. Backups use sorted compact JSON for their SHA-256 content hash; the hash detects accidental changes but is not authentication.
