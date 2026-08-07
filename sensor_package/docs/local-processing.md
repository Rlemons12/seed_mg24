# Local processing and profiles

Each channel keeps sample, processing, report, and heartbeat intervals independent. Available filters include bounded moving average, EMA, median, and debounce. Calibration and alarm hooks use explicit enabled flags; missing values are never interpreted as zero. Alarm events occur only on state transitions, and the priority buffer preserves alarm/fault/recovery records ahead of routine measurements.

Canonical built-in profile data is in `profiles/built_in`. It may describe only confirmed firmware behavior. Supporting a new physical sensor requires manufacturer, exact model, datasheet, interface, supply, output range, conditioning, measurement range/unit/accuracy, sampling/report needs, calibration, warning/alarm limits, hysteresis, persistence, and safety role.
