# QuietCool BLE — Home Assistant Integration

Native Bluetooth Low Energy integration for QuietCool attic and whole-house fans. Auto-discovers fans, enables full speed and smart-mode control, and exposes temperature, humidity, and timer sensors — all using the stock manufacturer firmware with no hardware modification required.

[![HACS Default](https://img.shields.io/badge/HACS-Default-blue.svg)](https://hacs.xyz)
[![Validate](https://github.com/rwarner/ha-quietcool-ble/actions/workflows/validate.yml/badge.svg)](https://github.com/rwarner/ha-quietcool-ble/actions/workflows/validate.yml)

## Status

**Hardware-confirmed working** on the AFG SMT PRO-2.0 (firmware V3.0) and on firmware 3.9+ / V4.1 controllers (e.g. the AFG SMT ES-3.0). Fan control, smart mode, temperature, humidity, timers, and threshold configuration are verified on real hardware.

## Supported Devices

| Model | CFM | Speeds | BLE Name | Status |
|---|---|---|---|---|
| AFG SMT PRO-2.0 Smart Attic Fan | 1945 | Low / High | `ATTICFAN_*` | ✅ Hardware confirmed (firmware V3.0) |
| AFG SMT ES-3.0 | 2801 | Low / Med† / High | `ATTICFAN_*` | ✅ Hardware confirmed (firmware V4.1) |
| QC ES-3100 Whole House Fan‡ | 3081 | Low / High | `ATTICFAN_*` | ✅ Hardware confirmed (firmware V4.1) |
| AFG SMT ES-2.0 | Various | Low / High | `ATTICFAN_*` | 🔲 Protocol confirmed, untested |
| AFG SMT NR-A (2022 revision) | Various | Low / High | `ATTICFAN_*` | 🔲 Protocol confirmed, untested |
| Other ESP32-based QuietCool controllers | Various | Unknown | `ATTICFAN_*` | 🔲 Untested |

All supported controllers advertise over BLE with a name beginning with `ATTICFAN`. Some revisions omit the BLE local name and instead advertise the raw manufacturer-specific signature `3atticfan` (company ID `0x6133`, payload prefix `tticfan`); these are recognized too and show as `QuietCool Fan (<address>)` until their real name is read over GATT.

> ‡ **Whole house fans** work too, as long as they're driven by a QuietCool **Smart Attic Fan Controller**. The integration talks to the controller, not the fan, so any fan wired to a supported controller should work — the QC ES-3100 is the first confirmed whole-house setup ([#11](https://github.com/rwarner/ha-quietcool-ble/issues/11)).

> † **Medium speed** is offered automatically on 3-speed fans — the integration shows it only when the firmware reports a 3-speed type (`FanType: THREE`), so 2-speed fans are unaffected. Hardware-confirmed on the AFG SMT ES-3.0 (firmware V4.1) ([#4](https://github.com/rwarner/ha-quietcool-ble/issues/4)).

> **Firmware 3.9+ note:** All features — fan control, smart mode, temperature, humidity, timer, and threshold configuration — work on all supported firmware versions including 3.9+ / V4.x.

## What You Get

**Fan control**
- Turn on / off
- Low and High speed presets (plus Medium on 3-speed fans that report it)

**Timer**
- Set the run **duration** from HA (Timer Hours / Timer Minutes) — no app required
- Turn-on honors that duration instead of forcing the firmware's 8-hour default
- Timer countdown sensor (seconds remaining)

**Smart Mode (TH — Thermostat + Humidity)**
- Automatic on/off based on attic temperature and humidity thresholds
- Full threshold configuration from HA — no app required
- Mode selector: Idle / Timer / TH

**Sensors**
- Attic temperature in °F
- Attic humidity in %
- Timer countdown (seconds remaining)
- Protect temperature (overtemp safety cutoff — diagnostic)

**General**
- Auto-discovery — HA detects the fan automatically when in Bluetooth range
- BT Proxy support — works through [ESPHome Bluetooth Proxies](https://esphome.io/components/bluetooth_proxy.html) for extended range
- Firmware and hardware version shown in device info

## Prerequisites

- Home Assistant 2023.7 or newer
- Bluetooth adapter on your HA host, or an ESPHome BT Proxy on the same network
- QuietCool fan powered on and within Bluetooth range during initial setup

## Installation

### HACS (recommended)

This integration is in the **default HACS store**, so no custom repository is needed.

[![Open in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=rwarner&repository=ha-quietcool-ble&category=integration)

1. Open **HACS** in Home Assistant
2. Search for **QuietCool BLE**
3. Click it, then click **Download**
4. Restart Home Assistant

(Or use the **Open in HACS** button above to jump straight to the download page.)

<details>
<summary>Installing via custom repository (older HACS, or before the store updates)</summary>

If **QuietCool BLE** doesn't appear in search yet:

1. Open HACS → ⋮ (top right) → **Custom repositories**
2. Add `https://github.com/rwarner/ha-quietcool-ble` with category **Integration**
3. Search for **QuietCool BLE** and download it
4. Restart Home Assistant

</details>

### Manual

Copy `custom_components/quietcool_ble/` into your HA config's `custom_components/` directory and restart.

## Setup

When your fan is powered on and in BLE range, HA will show a notification:

> **New device discovered: QuietCool Fan**

1. Click **Configure** in the notification (or go to **Settings → Integrations → Add Integration → QuietCool BLE**)
2. Confirm the device name and MAC address shown
3. Trigger pairing mode on the fan controller (see below)
4. Click **Submit** in the HA UI
5. Done — all entities appear automatically

### Triggering Pair Mode

You have two options — use whichever is easier:

**Option A — QuietCool app (easiest, no ladder required):**
Open the QuietCool Smart Control app → tap your device → tap **Pair Mode**. The controller enters pairing mode without you needing to physically reach it. This is the recommended approach if the fan is mounted in an attic or high on a gable.

**Option B — Physical Pair button:**
Hold the Pair button on the wall control unit or controller board until the light flashes. It is typically labeled **"Pair"** or has a Bluetooth symbol. On the AFG SMT PRO-2.0 it is on the controller board inside the fan housing.

<details>
<summary>Using an existing Phone ID instead of pairing (advanced, optional)</summary>

The setup screen has an **optional Phone ID** field. Leave it blank and just pair — that is the right path for almost everyone. It exists only for the firmware 3.9+ / V4.x case where pairing a *new* ID sometimes fails, so you can log in with an ID the controller already trusts.

**The catch: the QuietCool app never shows its Phone ID anywhere in the UI, so there is nothing to look up there — don't go searching for it.** It is generated automatically and stored on the controller. There is no supported way to read it out of the app on iOS, and Home Assistant redacts it in downloaded diagnostics, so you can't read it back from a prior HA setup either.

In practice a Phone ID is only handy if you already have one from:
- a [CrazyCoder ESPHome native](https://github.com/CrazyCoder/quietcool-esphome-native) config (it stores the ID it uses), or
- a BLE capture of the app pairing/logging in (the ID is in the `Login` / `Pair` payload), or
- a previous setup where you wrote it down.

If you don't have one of those, ignore the field and pair normally. Any string of 8–100 letters, numbers, or hyphens is accepted.

</details>

## Entities

| Entity | Type | Unit | Notes |
|---|---|---|---|
| Fan | `fan` | — | On/off, `Low` / `High` speed preset (plus `Medium` on 3-speed fans) |
| Mode | `select` | — | `Idle` / `Timer` / `TH` (smart mode) |
| Humidity Fan Speed | `select` | — | Speed used on humidity-driven runs (`Low` / `High`, plus `Medium` on 3-speed) |
| Fan Speed | `sensor` | — | Physical speed: `Off` / `Low` / `Medium` / `High` |
| Temperature | `sensor` | °F | Attic temp: `Temp_Sample / 10` |
| Humidity | `sensor` | % | Attic humidity: direct integer |
| Timer Remaining | `sensor` | s | Countdown when in Timer mode |
| Protect Temperature | `sensor` | °F | Overtemp safety cutoff (diagnostic) |
| High Temp Threshold | `number` | °F | TH mode activates above this |
| Medium Temp Threshold | `number` | °F | 2-speed fans switch LOW→HIGH above this |
| Low Temp Threshold | `number` | °F | TH mode deactivates below this |
| Humidity Off Threshold | `number` | % | Fan **stops** at/above this humidity (cutout, checked first) |
| Humidity On Threshold | `number` | % | Fan **runs** above this humidity regardless of temp (blank = disabled) |
| Timer Hours | `number` | h | Timer-mode run duration (0–23); setting it doesn't start the fan |
| Timer Minutes | `number` | min | Timer-mode run duration (0–59); setting it doesn't start the fan |

## Timer Duration

The **Timer Hours** and **Timer Minutes** number entities set how long the fan runs when it's in Timer mode. They write only the stored duration (via `SetTime`) — changing them does **not** turn the fan on. When you next turn the fan on (or switch Mode to `Timer`), it counts down from this duration instead of the firmware's 8-hour default.

## Smart Mode (TH)

TH mode lets the fan controller automatically turn the fan on and off based on attic temperature and humidity. The thresholds are stored on the device and persist across power cycles and HA restarts.

The **Fan Speed** sensor shows whether the blades are actually spinning (`Off` / `Low` / `Medium` / `High`). In TH mode the fan entity shows as "on" (control mode is active), but Fan Speed will read `Off` whenever the current conditions haven't triggered it yet.

Select **TH** from the Mode dropdown to activate it. Adjust the threshold number entities to match your comfort targets — changes take effect immediately without restarting the fan.

**Humidity works as two separate thresholds** (matching the QuietCool app's "Turn Fan Off" / "Turn Fan On" labels — the wire field names are the inverse of what they sound like):
- **Humidity Off Threshold** (`GetHum_H`, factory 90%) — a high-humidity **cutout**, checked *first*: at or above this the fan **stops** regardless of temperature, so it won't run when outside air is too damp to help.
- **Humidity On Threshold** (`GetHum_L`, factory 70%) — the fan **turns on** above this humidity even if the temperature thresholds wouldn't, running at the **Humidity Fan Speed**. Leave blank (device value `255`) to disable; re-disabling requires the QuietCool app.

Example targets for a typical attic fan:
- High Temp: 85–95°F (fan turns on)
- Low Temp: 65–75°F (fan turns off)
- Humidity Off (cutout): 80–90%
- Humidity On (ventilate): 60–70%

## Automations

The temperature sensor and fan/mode entities work in any HA automation. A few examples:

<details>
<summary>Example automations (temperature-triggered speed, TH mode at sunset)</summary>

**Turn on at Low speed when attic exceeds 90°F:**
```yaml
automation:
  trigger:
    platform: numeric_state
    entity_id: sensor.attic_gable_fan_temperature
    above: 90
  action:
    service: fan.turn_on
    target:
      entity_id: fan.attic_gable_fan
    data:
      preset_mode: Low
```

**Switch to High speed above 100°F:**
```yaml
automation:
  trigger:
    platform: numeric_state
    entity_id: sensor.attic_gable_fan_temperature
    above: 100
  action:
    service: fan.turn_on
    target:
      entity_id: fan.attic_gable_fan
    data:
      preset_mode: High
```

**Turn off when temperature drops below 75°F:**
```yaml
automation:
  trigger:
    platform: numeric_state
    entity_id: sensor.attic_gable_fan_temperature
    below: 75
  action:
    service: fan.turn_off
    target:
      entity_id: fan.attic_gable_fan
```

**Activate TH smart mode at sunset:**
```yaml
automation:
  trigger:
    platform: sun
    event: sunset
  action:
    service: select.select_option
    target:
      entity_id: select.attic_gable_fan_mode
    data:
      option: TH
```

</details>

## Troubleshooting

**Fan not discovered:**
- Ensure the fan is powered on
- Check that your HA host has Bluetooth or an ESPHome BT proxy configured
- Try moving a BT proxy closer to the fan

**Pairing failed:**
- If using the physical button, **hold** it (don't just tap) until the light flashes, then click Submit in HA
- If the fan is hard to reach, use the QuietCool app instead: tap your device → **Pair Mode** (this opens a longer, more reliable pairing window than the physical button)
- On **firmware 3.9+ / V4.x** fans, pairing a *new* Phone ID can fail. If it does, enter an **existing Phone ID** (from a prior setup, an ESPHome config, or the QuietCool app) on the setup screen to skip pairing and just log in

**Pairing is "acknowledged" but never persists (entities stay unavailable):**
The controller stores a maximum of **50 Phone IDs**, and once that's full it will *acknowledge* a pair without actually storing it. Repeated pairing attempts can fill it up. Fix: **factory-reset the controller** to clear its pairing memory — via the QuietCool app (**Fan Settings**) or by holding the **Test/Speed** button on the controller until it beeps — then pair again. (Thanks to the community + [CrazyCoder's protocol docs](https://github.com/CrazyCoder/quietcool-esphome-native/blob/main/docs/OEM-BLE-PROTOCOL.md) for this one.)

**Integration shows "unavailable" after setup:**
- Power cycle the fan controller
- In HA: Settings → Integrations → QuietCool BLE → ⋮ → Reload

**Entities go "unavailable," or the app and Home Assistant seem to fight:**
The fan stores **multiple** Phone IDs, but only one device can be *connected* at a time — so using the QuietCool app can interrupt Home Assistant's connection (and vice-versa). Home Assistant reconnects on its own once the fan is free, as long as its Phone ID is still accepted.

If entities stay unavailable, Home Assistant's login is being rejected — its Phone ID is no longer registered (the pairing may not have persisted on firmware 3.9+, or the controller dropped it). Home Assistant then shows a **re-pair prompt** automatically when it detects this. You can also delete and re-add the integration and **enter a known Phone ID** on the setup screen to reconnect without re-pairing.

**Threshold changes not sticking:**
Thresholds are written with the `SetTempHumidity` command and confirmed with a `GetParameter` read on the next poll. If the UI shows the new value but the next poll reverts it, open an issue with your debug logs.

**Enabling debug logs:**

```yaml
logger:
  default: warning
  logs:
    custom_components.quietcool_ble: debug
```

Restart HA, then reproduce the problem. Logs appear in **Settings → System → Logs**. Each BLE command and its raw JSON response are logged at `DEBUG` level.

## Security

This integration communicates directly with your fan over Bluetooth Low Energy. Be aware:

- **No link-layer encryption.** BLE communication is unencrypted — a firmware limitation that cannot be fixed in the integration.
- **Shared access, no per-user auth.** The controller stores up to 50 pairing credentials (Phone IDs), so the QuietCool app and Home Assistant can both stay paired (only one connects at a time). Any BLE client within range that pairs can control the fan.

For home use the risk profile is similar to any locally-controlled smart home device.

## How It Works

QuietCool's ESP32-based BLE controllers advertise under names starting with `ATTICFAN` and speak JSON over a single GATT characteristic. There are two protocol versions (V1 below firmware 3.9, V2 at 3.9+), and the integration auto-detects which on first connection.

<details>
<summary>GATT characteristic, protocol examples, and threshold command</summary>

All communication uses a single GATT characteristic with JSON commands:

```
Service:  000000ff-0000-1000-8000-00805f9b34fb
Char:     0000ff01-0000-1000-8000-00805f9b34fb
```

Two protocol versions exist depending on firmware:

**V1 (firmware < 3.9)** — string command names, full response keys:
```json
→ {"Api": "GetWorkState"}
← {"Mode": "TH", "Range": "HIGH", "Temp_Sample": 908, "Humidity_Sample": 23}
```

**V2 (firmware ≥ 3.9)** — numeric command codes, single-character response keys, `QQ` prefix:
```json
→ {"A": 17}
← QQ{"A": 17, "N": "ATTICFAN_XXXX", "M": "...", "S": "..."}
```

**Smart mode thresholds (V1)** — written with `SetTempHumidity`; all six fields are required per poll:

```json
→ {"Api": "SetTempHumidity", "SetTemp_H": 86, "SetTemp_M": 75, "SetTemp_L": 65,
   "SetHum_H": 90, "SetHum_L": 255, "SetHum_Range": "LOW"}
← {"Api": "SetTempHumidity", "Flag": "TRUE"}
```

</details>

## Protocol Research

| Source | Contribution |
|---|---|
| [emerose/quietcool](https://github.com/emerose/quietcool) | Original V1 reverse-engineering: command names, response keys, `Temp_Sample / 10` formula |
| [alex-spyksma/quietcool](https://github.com/alex-spyksma/quietcool/tree/issue/3-cannot-import-main) | Additional commands: `GetVersion`, `GetRemainTime`, `GetParameter`, `SetTempHumidity` |
| [u/secretoftheeast on Reddit](https://www.reddit.com/r/homeassistant/comments/1kyv0pn/quietcool_whole_house_fan_home_assistant/) | Discovered firmware 3.9+ V2 protocol: `QQ` prefix, numeric codes, single-character keys |
| [@DillonBrown](https://github.com/DillonBrown) | Full V2 API code mapping from QuietCool Smart Control Android app 2.0.28; hardware-confirmed on V4.1 firmware |
| [CrazyCoder/quietcool-esphome-native](https://github.com/CrazyCoder/quietcool-esphome-native) | Authoritative [OEM BLE protocol documentation](https://github.com/CrazyCoder/quietcool-esphome-native/blob/main/docs/OEM-BLE-PROTOCOL.md) — confirmed the pairing/login sequence, `PairState` semantics, the 50-PhoneID storage limit, and the full API code table |
| [HA Community thread](https://community.home-assistant.io/t/quietcool-integration/913242) | Community reports and device compatibility |

## Changelog

Full release history is in [CHANGELOG.md](CHANGELOG.md). Most recent release:

**v0.2.19** — corrects the humidity smart-mode thresholds (the `GetHum_H` cutout was mislabeled as a turn-on trigger), exposes the missing **Humidity On Threshold** and a **Humidity Fan Speed** select, and raises the Low Temp max to 115°F. Thanks [@evan](https://github.com/evan) ([#16](https://github.com/rwarner/ha-quietcool-ble/pull/16), [#17](https://github.com/rwarner/ha-quietcool-ble/pull/17)).

**v0.2.18** — set the timer duration from Home Assistant: new **Timer Hours** / **Timer Minutes** number entities, and turn-on now honors that duration instead of forcing the firmware's 8-hour default ([#15](https://github.com/rwarner/ha-quietcool-ble/issues/15)).

**v0.2.17** — also discovers controllers that omit the BLE local name and advertise the `3atticfan` manufacturer-data signature instead, so those name-less revisions auto-discover and appear in the manual picker. Thanks [@viss](https://github.com/viss/ha-quietcool-ble) for reverse-engineering the variant.

**v0.2.16** — fixes a D-Bus connection leak that could permanently kill Bluetooth after a few hours (`Bad file descriptor` / `EOFError`, recovers only on a full HA restart). The integration now closes Bleak's per-connection D-Bus bus when the fan drops the link. Thanks [@brian316](https://github.com/brian316) ([#13](https://github.com/rwarner/ha-quietcool-ble/pull/13)).

## Related Projects

- [CrazyCoder/quietcool-esphome-native](https://github.com/CrazyCoder/quietcool-esphome-native) — ESPHome **replacement firmware**: native Home Assistant over Wi-Fi with the stock QuietCool app still working via BLE, flashable in the browser, and reversible from HA. **A strong option if BLE pairing on newer firmware is giving you trouble** — no PhoneID pairing dance
- [emerose/quietcool](https://github.com/emerose/quietcool) — Python BLE CLI tool; primary protocol reference
- [alex-spyksma/quietcool](https://github.com/alex-spyksma/quietcool) — fork with additional command documentation
- [snyamathi/quietcool](https://github.com/snyamathi/quietcool) — emerose fork adding firmware 3.9+/V2 support
- [awkaplan/quietcool-esphome](https://github.com/awkaplan/quietcool-esphome) — earlier ESPHome firmware replacement (V1-era)
- [stabbylambda/homeassistant-quietcool](https://github.com/stabbylambda/homeassistant-quietcool) — earlier HA integration attempt (cloud-based)
