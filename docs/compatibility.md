# Compatibility and updates

Gateway `0.1.0` accepts protocol `1.0.0` and sensor packages from `0.1.0` up to, but excluding, `1.0.0`. Connection metadata is classified as `compatible`, `sensor_update_required`, `gateway_update_required`, `protocol_unsupported`, `metadata_missing`, or `unknown`. Diagnostics remain visible when incompatible; configuration operations must not assume compatibility.

Firmware updates are deliberate USB operations: back up readable configuration, review release metadata, compile/select a verified artifact, flash, read identity and versions, check BLE services/capabilities/telemetry, restore compatible configuration, and record verification. A later connection is not proof that flashing succeeded. OTA support is neither implemented nor claimed.
