# MG24 live and edge-summary telemetry

The sensor starts in `EDGE_SUMMARY` mode after every boot. An operator can use the device dashboard to select **Go Live** for high-rate troubleshooting and **Use Edge Summary** when finished. These controls send `MODE LIVE` and `MODE EDGE_SUMMARY` through the same-origin-protected device-command path; they do not reset, provision, or change device identity.

The production firmware currently builds with `ENABLE_MIC=0`. The microphone driver, hardware object, initialization, and continuous sampling calls are excluded, and the node does not advertise a microphone capability. The legacy channel configuration remains as the persisted telemetry timing container; it does not activate microphone hardware.

## Modes

`LIVE` publishes telemetry at the configured report interval. It is intended for short diagnostic sessions where the dashboard needs rapid updates.

`EDGE_SUMMARY` continues sampling and processing on the MG24, but accumulates battery voltage, microphone level, accelerometer axes, gyroscope axes, and analog inputs. Every 60 seconds it publishes the arithmetic average plus the number of contributing samples in telemetry field `sc`. Vibration processing continues on the board, while BLE vibration summaries are limited to once per summary interval. The heartbeat interval is at least five minutes in this mode.

The 60-second summary and five-minute heartbeat values are initial power-policy heuristics (`EDGE_SUMMARY_INTERVAL_MS` and `EDGE_HEARTBEAT_INTERVAL_MS`), not battery specifications. Changing into either mode clears an incomplete accumulator so a summary never mixes samples taken under two modes. The mode is deliberately runtime-only; rebooting safely returns the sensor to `EDGE_SUMMARY`.

## Energy and measurement limits

Edge summarization is expected to reduce energy used by BLE notifications and gateway traffic. Sampling, IMU processing, the BLE connection, and other enabled peripherals still consume power, so the exact saving must be measured on physical hardware. Longer sampling intervals and selectively powering peripherals down could save more, but require separate validation because they can reduce event fidelity.

An average can hide short peaks. Use `LIVE` for troubleshooting transient signals and the existing vibration peak/RMS processing for vibration events. Edge summaries do not create a calibrated battery percentage. Battery voltage remains measured, and recharge-time estimates remain gateway-side estimates based on voltage history and observed charge cycles.

## Physical validation

Measure current at the battery in both modes using the same sensor configuration and workload. Record average and peak current, BLE connection parameters, notification counts, IMU/microphone duty cycle, and runtime across several real charge cycles. Only those measurements can establish the actual energy saving.
