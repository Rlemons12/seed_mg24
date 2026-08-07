# Build and flash

Install Arduino CLI, `SiliconLabs:silabs`, and `LSM6DS3` with the setup wrapper. Copy the local device configuration example, assign and inventory a unique node identity, then compile with the component wrapper. The wrapper reads component/protocol versions, enables `protocol_stack=ble_silabs`, identifies dirty builds, and writes output only under `sensor_package/build/`.

Flash requires an explicit port and local identity. After USB upload, read back serial identity/version output, confirm BLE advertising, read metadata/capabilities, receive telemetry, and compare configuration. A BLE connection alone is not flash verification. Back up readable configuration and the node identity inventory before an update. OTA is unsupported.
