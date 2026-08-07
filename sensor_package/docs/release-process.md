# Sensor release process

Update only `sensor_package/VERSION`, `CHANGELOG.md`, and `RELEASE_INFO.json` for a sensor behavior/package release. Verify protocol compatibility, run native tests, compile with the documented FQBN, verify on hardware, then package the compiled artifact. The packaging script emits a manifest and SHA-256 checksum into ignored `dist/` and refuses to overwrite an existing release directory.

The manifest says `compiled-unverified` until a separate physical verification record exists. Core and library versions must be pinned before claiming reproducible builds. No tag or GitHub release is created by the script.

Release verification includes key-registry/reset-scope validation and USB bootstrap fixtures. CRC and mock interrupted-write tests are not substitutes for board power-loss testing. Never package device backups, local identities, serial logs, or `test_output/`.
