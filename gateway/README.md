# Raspberry Pi gateway 0.1.0

The dashboard uses a reusable, responsive module shell for overview, devices,
installations, firmware, and system-health surfaces. See
[`docs/frontend-module-template.md`](docs/frontend-module-template.md) before
adding a new page or navigation item.

This component owns BLE discovery and connections, the node and attached-sensor registry, declarative profile import, provisioning, SQLite history, FastAPI, WebSockets, and the browser dashboard. It contains no Arduino source or firmware build logic.

Battery charge-cycle runtime, degradation, recharge-window, and replacement planning are documented in [`../docs/battery-runtime-monitoring.md`](../docs/battery-runtime-monitoring.md). Battery percentage remains unavailable until a physical chemistry/discharge model is validated.

## Sensor Data Storage

SQLite is the authoritative local/edge database on the Raspberry Pi. PostgreSQL is not required on the Raspberry Pi. By default the gateway opens `sqlite:///./data/seed_mg24.db`; set `SEED_MG24_DATABASE_URL` to another durable local SQLite path when required. The path is relative to the gateway process working directory. Stop the gateway before copying the database for an offline backup, and copy the database together with its `-wal` and `-shm` files if it cannot be cleanly stopped. A gateway intended for sustained high-rate history should use storage designed for its write volume and endurance rather than relying indefinitely on a low-quality SD card.

The database stores registered nodes, installations, pinned sensor-profile metadata, lifecycle/provisioning history, and normalized telemetry readings. The existing `device_id` is the permanent MG24 `node_id`; an installation has its own immutable `installation_id` and equipment-facing `device_id`. Each reading snapshots the installation and physical `interface_id` that applied at collection time, so later installation rename, disable, archive, or replacement does not reinterpret history. Channels are strings rather than fixed columns, allowing built-in and future external-sensor channels without a table redesign.

Each reading has a local integer primary key plus a globally useful `reading_uuid` and the gateway's durable `gateway_id`. A gateway UUID is generated once and stored in the `gateway_identity` table. `SEED_MG24_GATEWAY_ID` may set it only when initializing a new database; a conflicting later value stops startup rather than silently changing historical identity. These fields are intended to support a future idempotent central importer keyed by `reading_uuid`; synchronization is not implemented yet.

Firmware currently reports sequence number and milliseconds since boot, not absolute UTC. `received_at` is the authoritative gateway receipt timestamp in UTC. `measured_at` is nullable and, when uptime exists, is an approximate wall-clock value derived from the gateway's first receipt/uptime anchor for that boot session. `device_uptime_ms` remains the source device time and is never represented as absolute UTC. Readings also retain raw and normalized numeric values, unit, existing firmware quality, delayed status, record type, and a size-bounded validated source payload for diagnostics. The source payload limit is `SEED_MG24_MAX_PAYLOAD_JSON_BYTES`; raw capture is not an unbounded second packet log.

One BLE packet is normalized and persisted in one SQLAlchemy transaction, including all channel rows and applicable node/installation timestamps. Database work runs outside the async BLE callback thread. SQLite connections enable foreign keys, WAL, `synchronous=NORMAL`, and a 5-second busy timeout. Persistence failures are rolled back and logged with node, sequence, and channel context; a failed packet is not entered into the in-memory duplicate cache, so a retransmission can be stored. There is no unbounded memory queue.

History is available from `GET /api/devices/{node_id}/readings`. The existing `offset`, `limit`, `start`, `end`, and `channel` parameters remain supported. Additive `installation_id`, `interface_id`, opaque `cursor`, and `include_total` parameters support more focused/keyset queries. `SEED_MG24_HISTORY_PAGE_SIZE_MAX` defaults to 500 rows and `SEED_MG24_HISTORY_MAX_DAYS` defaults to 31 days per request. Set `include_total=false` for large histories to avoid an exact `COUNT(*)`; follow `next_cursor` for the next page.

Local retention is unlimited by default. `SEED_MG24_HISTORY_RETENTION_DAYS` is blank/disabled unless explicitly configured. When enabled, a background task deletes vibration windows and readings strictly older than the cutoff in independent batches controlled by `SEED_MG24_HISTORY_RETENTION_BATCH_SIZE` (default 1,000). Cleanup continues every five seconds while either table has eligible rows, then returns to the six-hour idle cadence. It does not delete node, installation, profile, audit, provisioning, configuration, lifecycle, battery-cycle, or battery-generation records.

The gateway runs a non-blocking SQLite `PASSIVE` WAL checkpoint every `SEED_MG24_SQLITE_WAL_CHECKPOINT_INTERVAL_SECONDS` (default 300 seconds). Checkpointing bounds the separate WAL file but does not shrink free pages in the main database. SQLite reuses those pages for later writes.

Live mode is intended only for bounded diagnostics. `SEED_MG24_LIVE_MODE_MAX_SECONDS` defaults to 600 seconds; after that interval the gateway commands the sensor back to Edge Summary. If it is disconnected at expiry, the fallback remains queued and is applied on the next observed telemetry wake. Edge Summary or Low Power should be the normal operating mode.

### Controlled SQLite compaction

Retention deletion does not reduce the database file size. Compact only during a planned outage with enough temporary free disk space for the live content of a second database:

1. Stop the gateway and verify that no gateway Python process remains.
2. Run `PRAGMA wal_checkpoint(TRUNCATE)` against the configured database.
3. Record application-table counts and run `PRAGMA integrity_check`.
4. Create a new file with `VACUUM INTO 'seed_mg24.compact.db'`; never overwrite the source directly.
5. Run `PRAGMA integrity_check` and `PRAGMA foreign_key_check` on the copy, and compare every application-table count.
6. Rename the original to a temporary pre-compaction name, move the validated copy into the configured path, and start the gateway.
7. Confirm `/api/health`, `/api/nodes`, fresh telemetry writes, and `PRAGMA quick_check` before removing the temporary original.

Do not run compaction while the gateway is active, do not copy only the main file while a WAL exists, and do not delete the original before validating the replacement.

### Storage growth

Current firmware defaults to a 100 ms report interval (10 packets/second). With microphone raw/percent, battery, LED state, six IMU axes, and D0-D5 enabled, a normal packet produces 16 reading rows. The following is a row-count projection, not a disk-size promise:

| Sensors | Readings/packet | Packets/sec | Rows/minute | Rows/hour | Rows/day | Rows/30 days | Rows/year |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 16 | 10 | 9,600 | 576,000 | 13,824,000 | 414,720,000 | 5,045,760,000 |
| 10 | 16 each | 100 total | 96,000 | 5,760,000 | 138,240,000 | 4,147,200,000 | 50,457,600,000 |
| 25 | 16 each | 250 total | 240,000 | 14,400,000 | 345,600,000 | 10,368,000,000 | 126,144,000,000 |
| 50 | 16 each | 500 total | 480,000 | 28,800,000 | 691,200,000 | 20,736,000,000 | 252,288,000,000 |
| 100 | 16 each | 1,000 total | 960,000 | 57,600,000 | 1,382,400,000 | 41,472,000,000 | 504,576,000,000 |

Actual delivery can be lower because of BLE connection/throughput limits, disconnects, disabled channels, configuration changes, or dropped packets. Disk usage must be measured on representative hardware: SQLite record/page overhead, text lengths, indexes, payload size, WAL checkpoint state, and page utilization all materially affect bytes per row. At the default rate, deliberate reporting intervals and a site-specific retention setting are essential before a large deployment; no deletion default is inferred from this estimate.

### Compact telemetry storage assessment

The normalized reading schema deliberately provides channel-level history, quality, units, installation/interface attribution, retention, and indexed queries. A compact packet table would reduce repeated packet metadata, but adding it alongside `readings` would increase storage rather than reduce it. Replacing normalized rows would require rewriting history APIs, battery queries, installation attribution, synchronization identity, retention, and dashboard consumers. Therefore this change does not duplicate packets into a second table. The safe near-term controls are Edge Summary/Low Power operation, bounded Live mode, one-day site retention, and compaction. A future schema migration should benchmark a packet-plus-columnar-summary design against those query requirements before replacing normalized rows.

The intended future topology is **SQLite at the edge + PostgreSQL centrally**. A future `TelemetrySyncService` can page unsynchronized local rows, send `gateway_id` plus `reading_uuid`, and let a central PostgreSQL service use idempotent conflict handling. Central credentials and PostgreSQL drivers are intentionally absent. Loss of LAN, Wi-Fi/WAP, or the future central server must never stop BLE acquisition or local SQLite collection.

## Relative vibration condition monitoring

Protocol 1.1 nodes may publish a separate compact vibration-window summary.
The normal 1.0 telemetry message remains unchanged, and nodes without the new
characteristic continue to work. The gateway decodes the summary into explicit
units and stores structured features in `vibration_windows`; it never stores the
400+ Hz raw IMU stream or the complete FFT spectrum.

Each active node/IMU installation learns an initial baseline from 100 valid
windows by default (about one minute at the current window cadence), then freezes
that baseline. Invalid acquisition windows and duplicates never update it.
Condition evaluation compares RMS, peak, crest factor, kurtosis, dominant
frequency/amplitude, and gyro angular-velocity RMS against that sensor and
installation's own baseline. Three consecutive evaluations are required for a
state transition. The states are `BASELINE_PENDING`, `NORMAL`, `ELEVATED`,
`SIGNIFICANT_CHANGE`, `INSUFFICIENT_DATA`, and `INVALID`. The optional
`baseline_similarity_score` means similarity to the learned baseline—not
percent machine health.

The latest incoming window is evaluated in memory, while structured history is
persisted at most every five seconds by default: approximately 17,280 records
per day per continuously connected sensor. The existing history-retention policy
also removes old vibration-window history in bounded batches when retention is
enabled. Baseline and condition records remain. Configure learning, hysteresis,
and persistence cadence with `SEED_MG24_VIBRATION_BASELINE_MINIMUM_WINDOWS`,
`SEED_MG24_VIBRATION_CONDITION_PERSISTENCE_WINDOWS`, and
`SEED_MG24_VIBRATION_PERSISTENCE_INTERVAL_SECONDS`.

Read-only routes provide the latest window, bounded history, baseline statistics,
baseline-version history, and current condition under
`/api/devices/{device_id}/vibration/...` and `/api/devices/{device_id}/condition`.
An operator can explicitly start a new learning cycle with confirmed POST
`/api/devices/{device_id}/vibration/baseline/relearn`. The prior version is
retained as `superseded`, the new version starts at zero in `building` state,
and the condition returns to `BASELINE_PENDING`. The older confirmed
`/baseline/reset` route remains a compatibility alias with the same
history-preserving semantics. Relearning is gateway analytics state: it does not
delete vibration/telemetry history and is independent of sensor identity,
firmware, provisioning, configuration, and factory reset.

This is relative condition monitoring. It is not calibrated vibration severity,
ISO 10816/20816 compliance, certified predictive maintenance, fault diagnosis,
or failure prediction. Algorithm version 1 identifies the current 256-sample,
2 Hz high-pass, Hann/FFT feature semantics so future algorithm changes do not
silently mix incompatible baselines.

Install with `python -m pip install -r gateway/requirements.txt`, copy `.env.example` to `.env`, and run `python -m gateway`. The dashboard listens on the configured host/port (default `0.0.0.0:8000`). Use `sudo bash gateway/scripts/install_raspberry_pi.sh` for the systemd installation. The service needs membership in the `bluetooth` group and access to BlueZ D-Bus.

Back up the configured SQLite file and `data/sensor_profiles/` together while the service is stopped. Sensor onboarding preserves separate node, installation, equipment, interface, and channel identities. Firmware compatibility is shown from metadata read over BLE; incompatible or missing metadata does not count as safe configuration compatibility.

The normal dashboard workflow treats the physical MG24 as a device first. **Add Sensor** scans USB and BLE, then offers exactly one safe action: install approved application firmware for a supported USB board, set up an authoritatively unassigned node, open a locally registered node, or import an assigned node from another database. Import is read-only on the sensor: it reads identity, metadata, capabilities, and persistent configuration, creates one local node record, and creates no installation record.

Device configuration is separate from optional installation/profile metadata. The current firmware persists one device-level processing record: microphone sample/process/filter settings plus the device reporting and heartbeat cadence. IMU, battery, and analog channels remain visible as live telemetry but do not claim separate persistent controls. Apply uses a bounded `CFGSET` transaction, correlated acknowledgement, and authoritative `PROVGET` readback; telemetry owns the BLE connection again afterward.

Only one gateway process may own a repository port at a time. Startup fails clearly when another instance already holds the process lock, preventing competing telemetry and configuration connections.

The Add Sensor dialog supports preflashed unassigned nodes and blank supported USB boards. An unassigned discovery uses an expiring transport identity, then becomes a permanent database record only after write-once BLE provisioning and readback. Firmware endpoints are loopback-only and reject arbitrary paths, arbitrary uploader arguments, bootloader images, hash mismatches, wrong boards, and protected-region overlap.

Application-only firmware installation preserves NVM3 identity and configuration. After installation, BLE assignment-state readback decides the next action: a node already in this database offers **Reconnect / View Sensor**; an assigned node absent from this database requires restoration/import of its original gateway database or the explicit USB application-factory recovery workflow. Ordinary Add Sensor never replaces identity, and deterministic assignment conflicts are displayed without automatic retry.

The Devices screen provides separate **Remove**, **Restore/Reapprove**, and **Factory Reset** actions. Removal archives gateway membership and installations while preserving telemetry and the physical sensor. Removed devices cannot reactivate through heartbeat, reconnect, import, or ordinary onboarding. Restore reuses the archived record after identity matching. Factory reset requires a loopback USB connection and verified immutable MCU hardware ID; it reports success only after reboot read-back and gateway cleanup. See [`../docs/sensor-lifecycle.md`](../docs/sensor-lifecycle.md).
