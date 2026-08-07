import argparse
import asyncio
import json
import math
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk

from serial import Serial, SerialException


DEFAULT_PORT = "COM3"
DEFAULT_BAUD = 115200
ANALOG_LABELS = [f"D{i}" for i in range(6)]
BLE_NAME = "XIAO-MG24-Sense"
BLE_SERVICE_UUID = "0100004d-4724-2480-2d4d-47240024beef"
BLE_TELEMETRY_UUID = "0200004d-4724-2480-2d4d-47240024beef"
BLE_COMMAND_UUID = "0300004d-4724-2480-2d4d-47240024beef"


class SerialWorker(threading.Thread):
    def __init__(self, port: str, baud: int, events: queue.Queue, commands: queue.Queue):
        super().__init__(daemon=True)
        self.port = port
        self.baud = baud
        self.events = events
        self.commands = commands
        self.stop_requested = threading.Event()
        self.serial = None

    def run(self):
        try:
            with Serial(
                self.port,
                baudrate=self.baud,
                timeout=0.1,
                write_timeout=0.5,
                dsrdtr=False,
                rtscts=False,
            ) as device:
                device.dtr = True
                device.rts = True
                self.serial = device
                self.events.put(("status", f"Connected to {self.port} at {self.baud} baud"))
                while not self.stop_requested.is_set():
                    self._write_pending_commands(device)
                    raw = device.readline()
                    if raw:
                        self.events.put(("line", raw.decode("utf-8", errors="ignore").strip()))
        except SerialException as exc:
            self.events.put(("status", f"Serial error: {exc}"))
        finally:
            self.serial = None
            self.events.put(("status", "Disconnected"))

    def _write_pending_commands(self, device: Serial):
        while True:
            try:
                command = self.commands.get_nowait()
            except queue.Empty:
                return
            device.write((command.strip() + "\n").encode("ascii"))

    def stop(self):
        self.stop_requested.set()


class BleWorker(threading.Thread):
    def __init__(self, target_name: str, events: queue.Queue, commands: queue.Queue):
        super().__init__(daemon=True)
        self.target_name = target_name
        self.events = events
        self.commands = commands
        self.stop_requested = threading.Event()
        self.last_line = None

    def run(self):
        try:
            asyncio.run(self._run())
        except Exception as exc:
            self.events.put(("status", f"BLE error: {exc}"))
        finally:
            self.events.put(("ble_connected_device", None))
            self.events.put(("status", "Disconnected"))

    async def _run(self):
        from bleak import BleakClient, BleakScanner

        self.events.put(("status", f"Scanning for {self.target_name}..."))
        device = await BleakScanner.find_device_by_filter(
            lambda d, adv: (d.name == self.target_name) or (adv.local_name == self.target_name),
            timeout=10.0,
        )
        if device is None:
            self.events.put(("status", f"BLE device not found: {self.target_name}"))
            return

        async with BleakClient(device) as client:
            connected_name = device.name or self.target_name
            self.events.put(("ble_connected_device", {"name": connected_name, "address": device.address}))
            self.events.put(("status", f"BLE connected to {connected_name} ({device.address})"))
            try:
                await client.start_notify(BLE_TELEMETRY_UUID, self._on_notify)
            except Exception:
                pass

            while not self.stop_requested.is_set():
                await self._write_pending_commands(client)
                await self._poll_telemetry(client)
                await asyncio.sleep(0.2)

    def _on_notify(self, _sender, data: bytearray):
        self._emit_data(data)

    async def _poll_telemetry(self, client):
        try:
            data = await client.read_gatt_char(BLE_TELEMETRY_UUID)
        except Exception:
            return
        self._emit_data(data)

    def _emit_data(self, data):
        line = bytes(data).decode("utf-8", errors="ignore").strip("\x00\r\n ")
        if line and line != self.last_line:
            self.last_line = line
            self.events.put(("line", line))

    async def _write_pending_commands(self, client):
        while True:
            try:
                command = self.commands.get_nowait()
            except queue.Empty:
                return
            await client.write_gatt_char(BLE_COMMAND_UUID, (command.strip() + "\n").encode("ascii"), response=True)

    def stop(self):
        self.stop_requested.set()


class Gauge(ttk.Frame):
    def __init__(self, parent, label: str, maximum: float):
        super().__init__(parent)
        self.maximum = maximum
        self.value = tk.StringVar(value="--")
        ttk.Label(self, text=label).grid(row=0, column=0, sticky="w")
        ttk.Label(self, textvariable=self.value, width=12, anchor="e").grid(row=0, column=1, sticky="e")
        self.bar = ttk.Progressbar(self, maximum=maximum)
        self.bar.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(2, 8))
        self.columnconfigure(0, weight=1)

    def set(self, value, suffix=""):
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            self.value.set("--")
            self.bar["value"] = 0
            return
        self.value.set(f"{numeric:.2f}{suffix}")
        self.bar["value"] = max(0, min(self.maximum, numeric))


class Dashboard(tk.Tk):
    def __init__(self, port: str, baud: int):
        super().__init__()
        self.title("XIAO MG24 Sense Dashboard")
        self.geometry("980x700")
        self.minsize(820, 580)

        self.events = queue.Queue()
        self.commands = queue.Queue()
        self.port = port
        self.baud = baud
        self.worker = None
        self.last_packet_at = None

        self.transport = tk.StringVar(value="hardwire")
        self.status = tk.StringVar(value="Connecting...")
        self.mode = tk.StringVar(value="Waiting for data")
        self.packet_age = tk.StringVar(value="--")
        self.mic_raw = tk.StringVar(value="--")
        self.imu_values = {name: tk.StringVar(value="--") for name in ("ax", "ay", "az", "gx", "gy", "gz", "pitch", "roll")}
        self.analog_values = [tk.StringVar(value="--") for _ in ANALOG_LABELS]
        self.ble_supported = tk.StringVar(value="--")
        self.ble_enabled = tk.StringVar(value="--")
        self.ble_connected = tk.StringVar(value="--")
        self.ble_name = tk.StringVar(value="--")
        self.ble_device = tk.StringVar(value="Not connected")
        self.wifi_supported = tk.StringVar(value="Unavailable")
        self.scan_status = tk.StringVar(value="Idle")

        self._build_ui()
        self._connect_transport()
        self.after(50, self._process_events)
        self.protocol("WM_DELETE_WINDOW", self._close)

    def _build_ui(self):
        root = ttk.Frame(self, padding=12)
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=1)
        root.columnconfigure(1, weight=1)
        root.rowconfigure(2, weight=1)

        header = ttk.Frame(root)
        header.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="XIAO MG24 Sense", font=("Segoe UI", 18, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(header, textvariable=self.status).grid(row=1, column=0, sticky="w")
        ttk.Label(header, textvariable=self.mode).grid(row=0, column=1, sticky="e")
        ttk.Label(header, textvariable=self.packet_age).grid(row=1, column=1, sticky="e")

        controls = ttk.LabelFrame(root, text="Controls", padding=10)
        controls.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        controls.columnconfigure(8, weight=1)
        ttk.Radiobutton(controls, text="Hardwire", variable=self.transport, value="hardwire", command=self._connect_transport).grid(row=0, column=0, padx=(0, 6))
        ttk.Radiobutton(controls, text="Bluetooth", variable=self.transport, value="bluetooth", command=self._connect_transport).grid(row=0, column=1, padx=(0, 12))
        ttk.Button(controls, text="Reconnect", command=self._connect_transport).grid(row=0, column=2, padx=(0, 12))
        ttk.Button(controls, text="LED Off", command=lambda: self.commands.put("LED OFF")).grid(row=0, column=3, padx=(0, 6))
        ttk.Button(controls, text="LED On", command=lambda: self.commands.put("LED ON")).grid(row=0, column=4, padx=(0, 12))
        ttk.Label(controls, text="Brightness").grid(row=0, column=5, padx=(0, 6))
        led = ttk.Scale(controls, from_=0, to=255, command=self._send_led)
        led.grid(row=0, column=6, sticky="ew")
        ttk.Button(controls, text="Ping", command=lambda: self.commands.put("PING")).grid(row=0, column=9, padx=(12, 0))

        sensors = ttk.LabelFrame(root, text="Built-In Sensors", padding=10)
        sensors.grid(row=2, column=0, sticky="nsew", padx=(0, 6))
        sensors.columnconfigure(0, weight=1)
        self.mic_gauge = Gauge(sensors, "Microphone", 100)
        self.mic_gauge.grid(row=0, column=0, sticky="ew")
        self.battery_gauge = Gauge(sensors, "Battery / VBAT", 5)
        self.battery_gauge.grid(row=1, column=0, sticky="ew")
        self.led_gauge = Gauge(sensors, "User LED", 255)
        self.led_gauge.grid(row=2, column=0, sticky="ew")

        imu = ttk.LabelFrame(sensors, text="IMU", padding=10)
        imu.grid(row=3, column=0, sticky="ew", pady=(6, 0))
        for i, label in enumerate(("Accel X", "Accel Y", "Accel Z", "Gyro X", "Gyro Y", "Gyro Z", "Pitch", "Roll")):
            key = ("ax", "ay", "az", "gx", "gy", "gz", "pitch", "roll")[i]
            ttk.Label(imu, text=label).grid(row=i, column=0, sticky="w")
            ttk.Label(imu, textvariable=self.imu_values[key], width=14, anchor="e").grid(row=i, column=1, sticky="e")
        imu.columnconfigure(1, weight=1)

        right = ttk.Frame(root)
        right.grid(row=2, column=1, sticky="nsew", padx=(6, 0))
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)

        pins = ttk.LabelFrame(right, text="Analog Pins", padding=10)
        pins.grid(row=0, column=0, sticky="ew")
        for i, label in enumerate(ANALOG_LABELS):
            row = i % 10
            col = (i // 10) * 2
            ttk.Label(pins, text=label).grid(row=row, column=col, sticky="w", padx=(0, 8))
            ttk.Label(pins, textvariable=self.analog_values[i], width=8, anchor="e").grid(row=row, column=col + 1, sticky="e", padx=(0, 18))

        wireless = ttk.LabelFrame(right, text="Wireless", padding=10)
        wireless.grid(row=1, column=0, sticky="nsew", pady=(10, 0))
        wireless.columnconfigure(1, weight=1)

        for row, (label, var) in enumerate((
            ("BLE support", self.ble_supported),
            ("BLE advertising", self.ble_enabled),
            ("BLE connected", self.ble_connected),
            ("BLE name", self.ble_name),
            ("Connected to", self.ble_device),
            ("WiFi", self.wifi_supported),
        )):
            ttk.Label(wireless, text=label).grid(row=row, column=0, sticky="w", padx=(0, 8))
            ttk.Label(wireless, textvariable=var).grid(row=row, column=1, sticky="w")

        ble_controls = ttk.Frame(wireless)
        ble_controls.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(8, 6))
        ttk.Button(ble_controls, text="BLE Start", command=lambda: self.commands.put("BLE START")).pack(side="left", padx=(0, 6))
        ttk.Button(ble_controls, text="BLE Stop", command=lambda: self.commands.put("BLE STOP")).pack(side="left", padx=(0, 6))
        ttk.Button(ble_controls, text="Scan BLE", command=self._scan_ble).pack(side="left")

        ttk.Label(wireless, textvariable=self.scan_status).grid(row=6, column=0, columnspan=2, sticky="w")
        self.ble_scan = tk.Text(wireless, height=8, wrap="none")
        self.ble_scan.grid(row=7, column=0, columnspan=2, sticky="nsew", pady=(4, 0))
        wireless.rowconfigure(7, weight=1)

        log_frame = ttk.LabelFrame(root, text="Serial Log", padding=8)
        log_frame.grid(row=3, column=0, columnspan=2, sticky="nsew", pady=(10, 0))
        root.rowconfigure(3, weight=1)
        self.log = tk.Text(log_frame, height=8, wrap="none")
        self.log.pack(fill="both", expand=True)

    def _send_led(self, value):
        self.commands.put(f"LED {int(float(value))}")

    def _connect_transport(self):
        self._disconnect_worker()
        while not self.commands.empty():
            try:
                self.commands.get_nowait()
            except queue.Empty:
                break

        if self.transport.get() == "bluetooth":
            self.worker = BleWorker(BLE_NAME, self.events, self.commands)
            self.status.set(f"Connecting over Bluetooth to {BLE_NAME}...")
        else:
            self.worker = SerialWorker(self.port, self.baud, self.events, self.commands)
            self.status.set(f"Connecting over hardwire {self.port}...")

        self.worker.start()

    def _disconnect_worker(self):
        if self.worker is not None:
            self.worker.stop()
            self.worker = None

    def _process_events(self):
        while True:
            try:
                kind, value = self.events.get_nowait()
            except queue.Empty:
                break

            if kind == "status":
                self.status.set(value)
            elif kind == "line" and value:
                self._handle_line(value)
            elif kind == "ble_scan":
                self._show_ble_scan(value)
            elif kind == "ble_scan_error":
                self.scan_status.set(f"Scan error: {value}")
            elif kind == "ble_connected_device":
                if value:
                    self.ble_device.set(f"{value['name']} ({value['address']})")
                else:
                    self.ble_device.set("Not connected")

        if self.last_packet_at:
            self.packet_age.set(f"Last packet {time.time() - self.last_packet_at:.1f}s ago")
        self.after(50, self._process_events)

    def _handle_line(self, line: str):
        self.last_packet_at = time.time()
        self._append_log(line)

        if line.startswith("{"):
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                return
            if payload.get("type") == "telemetry" or payload.get("t") == "tele":
                self._show_json_telemetry(self._normalize_telemetry(payload))
            elif payload.get("type") == "boot" and payload.get("step") == "ble":
                self.ble_supported.set(self._yes_no(payload.get("supported")))
                self.ble_enabled.set(self._yes_no(payload.get("enabled")))
            elif payload.get("type") == "hello":
                self.mode.set(payload.get("board", "JSON firmware"))
            return

        try:
            value = float(line)
        except ValueError:
            return
        self.mode.set("Factory microphone firmware")
        self.mic_raw.set(str(int(value)))
        self.mic_gauge.set(max(0, min(100, (value - 735) * 100 / (900 - 735))), "%")

    def _show_json_telemetry(self, payload: dict):
        self.mode.set("Full telemetry firmware")
        self.mic_raw.set(str(payload.get("mic", "--")))
        self.mic_gauge.set(payload.get("mic_pct"), "%")
        self.battery_gauge.set(payload.get("battery_v"), " V")
        self.led_gauge.set(payload.get("led"))
        self.ble_supported.set(self._yes_no(payload.get("ble_supported")))
        self.ble_enabled.set(self._yes_no(payload.get("ble_enabled")))
        self.ble_connected.set(self._yes_no(payload.get("ble_connected")))
        self.ble_name.set(str(payload.get("ble_name", "--")))
        self.wifi_supported.set("Available" if payload.get("wifi_supported") else "Unavailable")

        accel = payload.get("accel") or {}
        gyro = payload.get("gyro") or {}
        ax, ay, az = (float(accel.get(k, 0)) for k in ("x", "y", "z"))
        pitch = math.degrees(math.atan2(-ax, math.sqrt(ay * ay + az * az)))
        roll = math.degrees(math.atan2(ay, az))

        values = {
            "ax": f"{ax:.3f} g",
            "ay": f"{ay:.3f} g",
            "az": f"{az:.3f} g",
            "gx": f"{float(gyro.get('x', 0)):.2f} dps",
            "gy": f"{float(gyro.get('y', 0)):.2f} dps",
            "gz": f"{float(gyro.get('z', 0)):.2f} dps",
            "pitch": f"{pitch:.1f} deg",
            "roll": f"{roll:.1f} deg",
        }
        for key, value in values.items():
            self.imu_values[key].set(value)

        for var, value in zip(self.analog_values, payload.get("analog") or []):
            var.set(str(value))

    def _normalize_telemetry(self, payload: dict) -> dict:
        if payload.get("type") == "telemetry":
            return payload

        accel = payload.get("a") or [0, 0, 0]
        gyro = payload.get("g") or [0, 0, 0]
        return {
            "type": "telemetry",
            "ms": payload.get("ms"),
            "mic": payload.get("m"),
            "mic_pct": payload.get("mp"),
            "battery_v": payload.get("bv"),
            "led": payload.get("l"),
            "ble_supported": bool(payload.get("bs")),
            "ble_enabled": bool(payload.get("be")),
            "ble_connected": bool(payload.get("bc")),
            "ble_name": BLE_NAME,
            "wifi_supported": False,
            "imu_ok": bool(payload.get("imu")),
            "mic_ok": bool(payload.get("mk")),
            "accel": {"x": accel[0], "y": accel[1], "z": accel[2]},
            "gyro": {"x": gyro[0], "y": gyro[1], "z": gyro[2]},
            "analog": payload.get("n") or [],
        }

    def _scan_ble(self):
        self.scan_status.set("Scanning...")
        self.ble_scan.delete("1.0", "end")
        threading.Thread(target=self._scan_ble_worker, daemon=True).start()

    def _scan_ble_worker(self):
        try:
            from bleak import BleakScanner

            async def scan():
                return await BleakScanner.discover(timeout=5.0, return_adv=True)

            results = asyncio.run(scan())
            rows = []
            for device, advertisement in results.values():
                name = device.name or advertisement.local_name or "(unnamed)"
                rssi = getattr(advertisement, "rssi", None)
                service_uuids = ", ".join(advertisement.service_uuids[:2])
                rows.append((name, device.address, rssi, service_uuids))
            rows.sort(key=lambda item: (item[0].lower(), item[1]))
            self.events.put(("ble_scan", rows))
        except Exception as exc:
            self.events.put(("ble_scan_error", str(exc)))

    def _show_ble_scan(self, rows):
        self.ble_scan.delete("1.0", "end")
        for name, address, rssi, service_uuids in rows:
            signal = "--" if rssi is None else f"{rssi} dBm"
            suffix = f"  {service_uuids}" if service_uuids else ""
            self.ble_scan.insert("end", f"{name}  {address}  {signal}{suffix}\n")
        self.scan_status.set(f"Found {len(rows)} BLE device(s)")

    @staticmethod
    def _yes_no(value):
        if value is True:
            return "Yes"
        if value is False:
            return "No"
        return "--"

    def _append_log(self, line: str):
        self.log.insert("end", line + "\n")
        self.log.see("end")
        if int(self.log.index("end-1c").split(".")[0]) > 300:
            self.log.delete("1.0", "80.0")

    def _close(self):
        self._disconnect_worker()
        self.destroy()


def parse_args():
    parser = argparse.ArgumentParser(description="Display Seeed XIAO MG24 Sense serial telemetry.")
    parser.add_argument("--port", default=DEFAULT_PORT)
    parser.add_argument("--baud", type=int, default=DEFAULT_BAUD)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    Dashboard(args.port, args.baud).mainloop()
