# Firmware architecture

The sketch schedules acquisition, processing, reporting, and heartbeat work independently with rollover-safe unsigned timing. Focused C++ modules own filters, channel processing/features, alarm transitions, bounded priority buffering, encoding, and validated configuration state. BLE transports compact records and exposes metadata/capabilities; it does not own sensor conversion logic. Fixed-capacity storage avoids allocation in the normal sampling loop.

The Raspberry Pi is the authoritative historical store. The MG24 owns time-sensitive sampling, filtering, transition detection, immediate events, and short disconnection buffering. Unknown external analog inputs remain disabled and use `adc_count` only when explicitly configured.
