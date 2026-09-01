Native Bluetooth Low Energy integration for QuietCool attic and whole-house fans. Auto-discovers fans, enables full speed and smart-mode control, and exposes temperature, humidity, and timer sensors — all using the stock manufacturer firmware with no hardware modification required.

## Features

- **Auto-discovery** — HA detects the fan automatically when in Bluetooth range (including controller revisions that advertise no BLE name, via their `3atticfan` manufacturer-data signature)
- **Fan control** — turn on/off, select Low or High speed
- **Timer duration** — set the run duration from HA (Timer Hours / Timer Minutes); turn-on honors it instead of forcing the firmware's 8-hour default
- **Smart Mode (TH)** — automatic on/off based on configurable temperature and humidity thresholds
- **Mode selector** — switch between Idle, Timer, and TH smart mode
- **Threshold controls** — set High/Medium/Low temp thresholds, the humidity turn-off cutout and turn-on trigger, and the humidity fan speed from HA
- **Temperature sensor** — attic temperature in °F
- **Humidity sensor** — attic humidity in %
- **Timer Remaining sensor** — countdown in seconds when in Timer mode
- **BT Proxy support** — works through ESPHome Bluetooth Proxies for extended range

## Supported Devices

All QuietCool ESP32-based controllers that advertise over BLE with a name beginning with `ATTICFAN` (plus name-less revisions that advertise the `3atticfan` manufacturer-data signature):

- AFG SMT PRO-2.0 Smart Attic Fan ✅ Hardware confirmed
- AFG SMT ES-3.0 (3-speed) ✅ Hardware confirmed
- QC ES-3100 Whole House Fan ✅ Hardware confirmed
- AFG SMT ES-2.0
- AFG SMT NR-A (2022 revision)

## Setup

When your fan is powered on and in BLE range, HA will show a discovery notification. Click Configure, then put the fan in **Pair Mode** (QuietCool app → Pair Mode, or hold the physical **Pair button** until the light flashes) and click Submit.

> **Note:** the fan stores **multiple** Phone IDs but only one device connects at a time, so the QuietCool app and Home Assistant can interrupt each other's connection. If Home Assistant stays unavailable it will prompt you to re-pair — or you can enter an existing Phone ID during setup to skip pairing.
