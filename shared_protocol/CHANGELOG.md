# Shared protocol changelog

## 1.1.0

- Add the optional `0700004d-4724-2480-2d4d-47240024beef` vibration-summary
  characteristic and schema-v1 compact feature window.
- Define vibration algorithm version 1 for relative condition monitoring. It
  is not calibrated severity or mechanical-fault classification.

## 1.0.0

- Define the initial versioned telemetry, metadata, capability, command, and configuration contracts.
# Unreleased

- Added the optional protocol-1.x read-only BLE onboarding identity characteristic and schema. It exposes a
  domain-separated 128-bit hardware-derived correlation value only in unprovisioned/recovery mode and removes it after
  provisioning.
