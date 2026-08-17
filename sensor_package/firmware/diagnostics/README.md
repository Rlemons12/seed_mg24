# XIAO MG24 maintenance diagnostics

These sketches isolate hardware and startup layers without using production
provisioning or configuration commands. They are intended for bounded bench
diagnostics, not production deployment.

| Sketch | Stack option | Purpose |
| --- | --- | --- |
| `xiao_mg24_diagnostic` | `none` or `ble_silabs` | Verify Arduino startup, USB serial, `Serial1`, timing, and the onboard LED. |
| `xiao_mg24_ble_advertising_diagnostic` | `ble_silabs` | Verify BLE system-boot dispatch and minimal legacy advertising. |
| `xiao_mg24_imu_diagnostic` | `none` | Verify the onboard IMU can initialize and return samples. |
| `xiao_mg24_vibration_diagnostic` | `none` | Characterize sustained six-axis IMU acquisition timing and calculate bounded vibration statistics. |
| `xiao_mg24_microphone_diagnostic` | `none` | Verify microphone initialization and sampling without BLE. |
| `xiao_mg24_microphone_ble_diagnostic` | `ble_silabs` | Verify microphone sampling while the BLE stack is active. |
| `xiao_mg24_nvm_read_diagnostic` | `ble_silabs` | Initialize the production NVM3 backend and read identity/configuration status without changing records. |

Build a sketch with Arduino CLI and a separate build directory. For example:

```powershell
arduino-cli compile `
  --fqbn "SiliconLabs:silabs:xiao_mg24:protocol_stack=ble_silabs" `
  --build-path sensor_package/build_diagnostic_ble `
  sensor_package/firmware/diagnostics/xiao_mg24_ble_advertising_diagnostic
```

Use `protocol_stack=none` for the non-BLE sketches shown above. Building does
not access hardware. Uploading remains a separate physical operation and must
use the primary application image, never a `with_bootloader` artifact.

The NVM read diagnostic is intentionally read-only at the application level.
It must not gain provisioning, reset, delete, or write operations. Status
values report persistence health without printing stored identity or
configuration contents.

The vibration diagnostic is self-contained and does not use BLE, NVM, or the
production protocol. Build it with:

```powershell
arduino-cli compile `
  --fqbn "SiliconLabs:silabs:xiao_mg24:protocol_stack=none" `
  --libraries sensor_package/firmware/libraries `
  --build-path sensor_package/build_diagnostic_vibration `
  sensor_package/firmware/diagnostics/xiao_mg24_vibration_diagnostic
```

See [Vibration acquisition diagnostic](../../docs/vibration-diagnostic.md) for
the serial commands, timing methodology, memory estimates, and limitations.

For continuous vibration acquisition, use `FIFO_TRANSPORT_TEST` before
`FIFO_CONTINUOUS_TEST`. The latter drains bounded 16-frame FIFO batches into
the two raw windows while the other window is processed. The longer
`FIFO_CONTINUOUS_LONG_TEST` runs 100 windows and should only be used after the
10-window command reports zero drops, overruns, alignment errors, and read
errors. `I2C_TIMING_TEST` compares the library's two-transfer reads with the
combined repeated-start reader without printing inside the timed loops.

Compile it for the XIAO MG24 Sense with the Silicon Labs BLE stack enabled:

```powershell
arduino-cli compile `
  --fqbn "SiliconLabs:silabs:xiao_mg24:protocol_stack=ble_silabs" `
  --build-path sensor_package/build_diagnostic_nvm_read `
  --build-property "compiler.cpp.extra_flags=-I$((Resolve-Path sensor_package/firmware/xiao_mg24_sensor_node).Path)" `
  sensor_package/firmware/diagnostics/xiao_mg24_nvm_read_diagnostic
```

The sketch reuses the production persistence implementation directly. On a
board it reports structured backend, identity, and configuration status over
USB serial and `Serial1`, plus a heartbeat. It never prints record contents.
