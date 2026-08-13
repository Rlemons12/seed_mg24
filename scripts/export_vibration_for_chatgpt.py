"""Export shareable vibration condition-monitoring data from the gateway SQLite DB."""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path


def rows(connection: sqlite3.Connection, query: str, parameters: tuple = ()) -> list[dict]:
    return [dict(item) for item in connection.execute(query, parameters)]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=Path("gateway/data/seed_mg24.db"))
    parser.add_argument("--output", type=Path, default=Path("test_output/chatgpt_exports"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(f"file:{args.database.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    devices = rows(connection, """
        SELECT id, device_id, display_name, hardware_id, firmware_version,
               sensor_package_version, protocol_version
        FROM registered_devices WHERE archived = 0 ORDER BY device_id
    """)
    definitions = {
        "accel_rms_*_g": "High-pass-conditioned acceleration RMS by axis, in g.",
        "accel_peak_*_g": "Largest absolute conditioned acceleration by axis, in g.",
        "crest_*": "Peak divided by RMS; dimensionless impulsiveness indicator.",
        "kurtosis_*": "Population kurtosis; dimensionless waveform impulsiveness indicator.",
        "dominant_frequency_*_hz": "Strongest allowed 256-point Hann FFT bin, in Hz.",
        "dominant_amplitude_*_g": "Hann coherent-gain-compensated dominant-bin amplitude, in g.",
        "gyro_rms_*_dps": "Angular-velocity RMS by axis, in degrees/second.",
        "baseline_similarity_score": "100 closely matches baseline; 0 strongly differs. Not machine health.",
    }
    for device in devices:
        device_id = device.pop("id")
        vibration = rows(connection, """
            SELECT * FROM vibration_windows
            WHERE registered_device_id = ? ORDER BY observed_at
        """, (device_id,))
        for item in vibration:
            item.pop("registered_device_id", None)
        baseline = rows(connection, """
            SELECT baseline_version, algorithm_version, status, sample_count,
                   minimum_samples, created_at, established_at, statistics_json
            FROM vibration_baselines WHERE registered_device_id = ?
            ORDER BY updated_at DESC LIMIT 1
        """, (device_id,))
        if baseline:
            baseline[0]["statistics"] = json.loads(baseline[0].pop("statistics_json"))
        condition = rows(connection, """
            SELECT state, baseline_similarity_score, latest_window_sequence,
                   evaluated_at, pending_state, pending_count, factors_json
            FROM vibration_conditions WHERE registered_device_id = ? LIMIT 1
        """, (device_id,))
        if condition:
            condition[0]["factors"] = json.loads(condition[0].pop("factors_json"))
        document = {
            "export_schema": "seed_mg24_chatgpt_vibration_export_v1",
            "exported_at_utc": datetime.now(UTC).isoformat(),
            "interpretation": {
                "purpose": "Relative condition monitoring against this sensor's own baseline.",
                "limitations": [
                    "Not calibrated ISO vibration severity.",
                    "Not a machine-health percentage or probability of failure.",
                    "Do not infer a specific mechanical fault from these features alone.",
                ],
                "field_definitions": definitions,
            },
            "sensor": device,
            "current_baseline": baseline[0] if baseline else None,
            "current_condition": condition[0] if condition else None,
            "vibration_windows": vibration,
        }
        target = args.output / f"{device['device_id']}_vibration_for_chatgpt.json"
        target.write_text(json.dumps(document, indent=2, allow_nan=False), encoding="utf-8")
        print(f"{target} ({len(vibration)} windows)")


if __name__ == "__main__":
    main()
