# Device identity and application persistence

`node_id` is the mutable provisioned identity of the MG24 board. It is distinct from the immutable MCU `hardware_id`, an attached sensor's `device_id`, editable `display_name`, BLE address, and advertised name. New firmware starts in an unprovisioned bootstrap state and cannot emit production telemetry as `UNASSIGNED-MG24`. Existing onboarding may assign identity once; BLE commands cannot erase or factory-reset identity.

Silicon Labs core 4.0.0 initializes the default NVM3 instance before Arduino `setup()`. The package uses only named keys `0x0FF00` through `0x0FF06` in the core-documented user domain `0x00000-0x0FFFF`. Arduino EEPROM uses low keys `0x0000-0x0028`; Bluetooth stack state is outside the user domain. Key ownership and reset allowlists are defined once in `application_nvm_keys.h` and checked by tests. `0x0FF06` is the versioned, CRC-protected reset transaction marker and is deliberately excluded from the normal deletion loop.

Identity and configuration are separate A/B records. Each has a 24-byte, little-endian envelope containing magic, record type and schema, payload length, generation, flags, payload CRC32, and header CRC32. CRC32 detects accidental corruption; it is not authentication and does not protect against a malicious debugger. A new generation is written to the inactive slot and read back before activation. The old valid slot is retained. Boot selects the newest valid generation using rollover-safe comparison and reports absent, corrupt, unsupported, conflict, or recovery states explicitly.

The USB line format is `MG24BOOT1 <bounded JSON>`. Identity assignment requires an unprovisioned state. Reassignment requires the reviewed `application_factory` reset procedure; there is no hidden password or BLE route. Maintain an external inventory and backup, prevent duplicate IDs operationally, and restore a replacement identity only after retiring the original board.

Reset scopes are allowlists, never ranges:

- `configuration_only`: configuration A, configuration B, and staging; identity is preserved.
- `application_factory`: identity A/B, configuration A/B, staging, and store metadata.

The USB v2 factory-reset exchange binds a secure random challenge to its operation ID, immutable hardware ID, scope, and expiration. The marker survives the reset reboot and is removed only after boot verifies that all resettable keys remain absent and the device is safely unprovisioned. An interrupted, corrupt, or uncleared transaction stays in bootstrap-only mode.

Both preserve bootloader, installed firmware/version, immutable MCU identity, manufacturer and factory/radio calibration, security/debug configuration, Bluetooth storage, Arduino EEPROM keys, and all other NVM3 keys. The application owns no Bluetooth bonds and therefore performs no blanket bond deletion. Power-loss and marker-clear behavior is covered by host logic tests where a native compiler is available, but still requires controlled MG24 validation.
