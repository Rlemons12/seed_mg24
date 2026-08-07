# Raspberry Pi gateway 0.1.0

This component owns BLE discovery and connections, the node and attached-sensor registry, declarative profile import, provisioning, SQLite history, FastAPI, WebSockets, and the browser dashboard. It contains no Arduino source or firmware build logic.

Install with `python -m pip install -r gateway/requirements.txt`, copy `.env.example` to `.env`, and run `python -m gateway`. The dashboard listens on the configured host/port (default `0.0.0.0:8000`). Use `sudo bash gateway/scripts/install_raspberry_pi.sh` for the systemd installation. The service needs membership in the `bluetooth` group and access to BlueZ D-Bus.

Back up the configured SQLite file and `data/sensor_profiles/` together while the service is stopped. Sensor onboarding preserves separate node, installation, equipment, interface, and channel identities. Firmware compatibility is shown from metadata read over BLE; incompatible or missing metadata does not count as safe configuration compatibility.
