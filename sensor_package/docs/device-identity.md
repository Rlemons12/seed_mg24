# Device identity and application persistence

`node_id` permanently identifies the MG24 board. It is distinct from an attached sensor's `device_id`, editable `display_name`, BLE address, and advertised name. New firmware starts as `UNASSIGNED-MG24`; assignment is performed once through the framed USB bootstrap protocol. BLE commands cannot provision or erase identity.

Silicon Labs core 4.0.0 initializes the default NVM3 instance before Arduino `setup()`. The package uses only named keys `0x0FF00` through `0x0FF05` in the core-documented user domain `0x00000-0x0FFFF`. Arduino EEPROM uses low keys `0x0000-0x0028`; Bluetooth stack state is outside the user domain. Key ownership and reset allowlists are defined once in `application_nvm_keys.h` and checked by tests.

Identity and configuration are separate A/B records. Each has a 24-byte, little-endian envelope containing magic, record type and schema, payload length, generation, flags, payload CRC32, and header CRC32. CRC32 detects accidental corruption; it is not authentication and does not protect against a malicious debugger. A new generation is written to the inactive slot and read back before activation. The old valid slot is retained. Boot selects the newest valid generation using rollover-safe comparison and reports absent, corrupt, unsupported, conflict, or recovery states explicitly.

The USB line format is `MG24BOOT1 <bounded JSON>`. Identity assignment requires an unprovisioned state. Reassignment requires the reviewed `application_factory` reset procedure; there is no hidden password or BLE route. Maintain an external inventory and backup, prevent duplicate IDs operationally, and restore a replacement identity only after retiring the original board.

Reset scopes are allowlists, never ranges:

- `configuration_only`: configuration A, configuration B, and staging; identity is preserved.
- `application_factory`: identity A/B, configuration A/B, staging, and store metadata.

Both preserve bootloader, factory/radio calibration, security/debug configuration, Bluetooth storage, Arduino EEPROM keys, and all other NVM3 keys. Physical power-loss behavior still requires MG24 testing.
