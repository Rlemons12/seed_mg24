# Seeed Studio XIAO MG24 Sense Dashboard

This project reads telemetry from a Seeed Studio XIAO MG24 Sense over USB serial and displays the board outputs in a Python desktop dashboard.

## What It Shows

- Analog microphone level
- LSM6DS3 IMU accelerometer X/Y/Z
- LSM6DS3 IMU gyroscope X/Y/Z
- Estimated pitch and roll
- Battery / VBAT voltage
- User LED state and LED controls
- Analog values for pins D0-D5
- Bluetooth LE support, advertising state, connection state, and PC-side BLE scan results
- WiFi availability status
- Raw serial log

## Hardware

- Seeed Studio XIAO MG24 Sense
- USB-C cable with data support
- Windows host

The board was detected as:

- Device: `Seeed Studio XIAO MG24 (Sense) CMSIS-DAP`
- Serial port: `COM3`
- Baud rate: `115200`

## Python Setup

The project uses the local virtual environment in `.venv`.

Install dependencies:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Run the dashboard:

```powershell
.\.venv\Scripts\python.exe main.py --port COM3 --baud 115200
```

The dashboard asserts DTR/RTS when opening the serial port. This is required for reliable output from this board on Windows.

The dashboard has two transport options:

- `Hardwire`: receives full JSON telemetry over USB serial on `COM3`.
- `Bluetooth`: receives compact JSON telemetry over BLE from `XIAO-MG24-Sense`.

The same dashboard controls, including LED commands, work through the selected transport.

## Firmware

The telemetry firmware is here:

```text
firmware/xiao_mg24_sense_telemetry/xiao_mg24_sense_telemetry.ino
```

It streams JSON packets like:

```json
{
  "type": "telemetry",
  "ms": 2545,
  "mic": 735,
  "mic_pct": 0,
  "battery_v": 4.008,
  "led": 0,
  "imu_ok": true,
  "mic_ok": true,
  "accel": {"x": -0.0776, "y": -0.0034, "z": 1.0219},
  "gyro": {"x": -0.280, "y": -2.800, "z": 0.000},
  "analog": [620, 408, 304, 329, 269, 304]
}
```

## Flashing With Arduino CLI

Install Arduino CLI, then add the Silicon Labs board package URL:

```powershell
arduino-cli config init --overwrite
arduino-cli config add board_manager.additional_urls https://siliconlabs.github.io/arduino/package_arduinosilabs_index.json
arduino-cli core update-index
arduino-cli core install SiliconLabs:silabs
arduino-cli lib install "Seeed Arduino LSM6DS3"
```

Compile and upload the serial-only build:

```powershell
arduino-cli compile --fqbn SiliconLabs:silabs:xiao_mg24:protocol_stack=none firmware\xiao_mg24_sense_telemetry
arduino-cli upload -p COM3 --fqbn SiliconLabs:silabs:xiao_mg24:protocol_stack=none firmware\xiao_mg24_sense_telemetry
```

Compile and upload the Bluetooth LE build:

```powershell
arduino-cli compile --fqbn SiliconLabs:silabs:xiao_mg24:protocol_stack=ble_silabs firmware\xiao_mg24_sense_telemetry
arduino-cli upload -p COM3 --fqbn SiliconLabs:silabs:xiao_mg24:protocol_stack=ble_silabs firmware\xiao_mg24_sense_telemetry
```

Use `protocol_stack=ble_silabs` when you want BLE advertising and BLE telemetry output. Use `protocol_stack=none` for a smaller serial-only build.

## Serial Commands

The dashboard sends these commands to the board:

```text
LED ON
LED OFF
LED 0..255
RATE 50..5000
BLE START
BLE STOP
PING
```

`LED 255` is full brightness. `RATE` controls the telemetry interval in milliseconds.

## Wireless

The BLE-enabled firmware advertises as:

```text
XIAO-MG24-Sense
```

It exposes a custom telemetry GATT service:

```text
Service:   0100004d-4724-2480-2d4d-47240024beef
Telemetry: 0200004d-4724-2480-2d4d-47240024beef
Command:   0300004d-4724-2480-2d4d-47240024beef
```

The telemetry characteristic is readable and notifiable. The command characteristic accepts the same text commands used over serial, such as `LED ON`, `LED OFF`, and `LED 128`.

The dashboard also includes a PC-side BLE scanner. It scans nearby BLE advertisements using Windows Bluetooth and lists device name, address, signal, and advertised service UUIDs.

WiFi is shown as unavailable because the XIAO MG24 Sense supports 2.4 GHz multiprotocol radio features such as Bluetooth LE, Bluetooth mesh, Thread, Zigbee, and Matter over Thread, but it does not include a WiFi radio.

## Notes

- The factory firmware only streams microphone-like numeric values. IMU, battery, LED control, and analog display require flashing the telemetry sketch.
- The IMU enable pin is `PD5`, per Seeed's example code.
- The microphone uses `PC9` for data and `PC8` for power.
- Analog telemetry is intentionally limited to D0-D5. Reading D0-D18 caused the sketch to block on this board/core combination.
- The onboard LED is active-low, so the firmware handles polarity internally.
- Bluetooth requires compiling with `protocol_stack=ble_silabs`.

## Unplugging The Board

The flashed telemetry firmware stays on the XIAO MG24 Sense after unplugging it. It is stored in non-volatile flash memory on the board.

When the board is unplugged:

- The board powers off unless it has battery or another external power source.
- The Python dashboard disconnects from the board.
- Plugging the board back in runs the telemetry firmware automatically.
- BLE advertising restarts automatically when the firmware boots.
- Windows may assign a different serial port after reconnecting, such as `COM4` instead of `COM3`.

If the serial port changes, relaunch the dashboard with the new port:

```powershell
.\.venv\Scripts\python.exe main.py --port COM4 --baud 115200
```

The dashboard program itself stays on the computer in this project folder. It does not run on the board.

## Diagnostic Sketch

A minimal serial and LED test sketch is included:

```text
firmware/xiao_mg24_diagnostic/xiao_mg24_diagnostic.ino
```

Use it only when debugging basic upload, serial, or LED behavior.
