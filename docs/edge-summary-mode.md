# MG24 live and edge-summary telemetry

The sensor starts in `EDGE_SUMMARY` mode after every boot. An operator can use the Battery tab to select **Go Live**, **Use Edge Summary**, or **Use Low Power**. These controls send `MODE LIVE`, `MODE EDGE_SUMMARY`, or `MODE LOW_POWER` through the same-origin-protected device-command path; they do not reset, provision, or change device identity.

The production firmware currently builds with `ENABLE_MIC=0`. The microphone driver, hardware object, initialization, and continuous sampling calls are excluded, and the node does not advertise a microphone capability. The legacy channel configuration remains as the persisted telemetry timing container; it does not activate microphone hardware.

## Modes

`LIVE` publishes telemetry at the configured report interval. It is intended for short diagnostic sessions where the dashboard needs rapid updates.

`EDGE_SUMMARY` continues sampling and processing on the MG24, but accumulates battery voltage, microphone level, accelerometer axes, gyroscope axes, and analog inputs. Every 60 seconds it publishes the arithmetic average plus the number of contributing samples in telemetry field `sc`. Vibration processing continues on the board, while BLE vibration summaries are limited to once per summary interval. The heartbeat interval is at least five minutes in this mode.

`LOW_POWER` uses the Silicon Labs core's EM2-capable sleep path in one-second slices. The short slices allow BLE command and disconnect processing to remain responsive. The IMU rail and battery-divider rail are off between reports. Approximately every five minutes the firmware powers both rails, waits for stabilization, initializes the IMU, captures one battery/analog/IMU snapshot, publishes it, and powers the rails down again. Vibration FIFO processing and vibration summaries are paused, so this mode is for battery/runtime monitoring rather than continuous machine-condition monitoring.

Low-power mode is deliberately runtime-only. A reboot starts in `EDGE_SUMMARY`; a BLE disconnect also returns the firmware to `EDGE_SUMMARY`, preventing the gateway from displaying a stale low-power state after reconnection. Identity, configuration, telemetry sequencing, buffered records, and lifecycle state remain intact.

The 60-second summary, five-minute heartbeat, and five-minute low-power snapshot values are initial power-policy heuristics, not validated battery specifications. Changing modes clears an incomplete accumulator so a summary never mixes samples taken under two modes.

## Energy and measurement limits

Edge summarization is expected to reduce energy used by BLE notifications and gateway traffic. Sampling, IMU processing, the BLE connection, and other enabled peripherals still consume power, so the exact saving must be measured on physical hardware. Longer sampling intervals and selectively powering peripherals down could save more, but require separate validation because they can reduce event fidelity.

An average can hide short peaks. Use `LIVE` for troubleshooting transient signals and the existing vibration peak/RMS processing for vibration events. Edge summaries do not create a calibrated battery percentage. Battery voltage remains measured, and recharge-time estimates remain gateway-side estimates based on voltage history and observed charge cycles.

## Physical validation

Measure current at the battery in all three modes using the same sensor configuration and workload. Record average and peak current, BLE connection parameters, notification counts, IMU duty cycle, wake duration, and runtime across several real charge cycles. Confirm that the production board actually reaches EM2 between FreeRTOS/Bluetooth events. Only those measurements can establish the actual energy saving; the MCU's data-sheet sleep current is not a claim for complete-board consumption.
