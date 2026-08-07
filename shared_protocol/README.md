# Shared protocol 1.0.0

This directory is the authoritative data contract between the MG24 sensor package and the Raspberry Pi gateway. JSON Schemas define metadata, telemetry, capabilities, commands, and configuration. Fixtures must validate against the matching schema.

Backward-compatible optional fields may be added within protocol 1.x. Removing fields, changing meanings or units, tightening accepted values, or changing framing requires a new major version and compatibility-matrix update. Database and UI concepts do not belong here.
