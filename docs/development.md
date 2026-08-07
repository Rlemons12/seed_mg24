# Component development

- Gateway: `python -m pytest gateway/tests -q`
- Sensor native checks: `python -m pytest sensor_package/tests -q`
- Protocol/compatibility: `python scripts/verify_compatibility.py`
- Entire repository: `python scripts/verify_repository.py`
- Firmware: `sensor_package/scripts/compile.ps1` or `compile.sh`

Use commit scopes such as `sensor:`, `gateway:`, `protocol:`, `docs:`, `build:`, and `test:`. Change only the affected component version/changelog. Future tags are component-specific (`sensor-v0.1.0`, `gateway-v0.1.0`, `protocol-v1.0.0`); this migration creates none.
