# Shared protocol 1.0.0

This directory is the authoritative data contract between the MG24 sensor package and the Raspberry Pi gateway. JSON Schemas define metadata, telemetry, capabilities, commands, and configuration. Fixtures must validate against the matching schema.

Backward-compatible optional fields may be added within protocol 1.x. Removing fields, changing meanings or units, tightening accepted values, or changing framing requires a new major version and compatibility-matrix update. Database and UI concepts do not belong here.

USB bootstrap protocol v1 uses newline-delimited `MG24BOOT1 ` framing followed by bounded JSON. Requests and responses carry a schema version, bounded request ID, action, and correlated result/error. Canonical schemas cover bootstrap requests, responses, and node backups. No schema permits arbitrary NVM keys or broad erase operations. Backups use sorted compact JSON for their SHA-256 content hash; the hash detects accidental changes but is not authentication.
