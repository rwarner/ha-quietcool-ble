# Changelog

### v0.2.19
- Fix: **humidity smart-mode thresholds were mislabeled** — the device's `GetHum_H`/`GetHum_L` field names are the inverse of what they sound like. `GetHum_H` (factory 90%) is the **"Turn Fan Off"** cutout (the fan *stops* at/above it, checked first), not a turn-on trigger. Relabeled `High Humidity Threshold` → **Humidity Off Threshold**, and exposed the previously-missing **Humidity On Threshold** (`GetHum_L`, factory 70%; blank = disabled) so humidity-driven startup can be enabled from HA. Verified against CrazyCoder's OEM protocol docs, the QuietCool app help, and snyamathi/quietcool. Thanks [@evan](https://github.com/evan) ([#16](https://github.com/rwarner/ha-quietcool-ble/pull/16))
- Feat: new **Humidity Fan Speed** select (`GetHum_Range`) — the speed the fan uses on humidity-driven runs (`Medium` offered only on 3-speed fans). Thanks [@evan](https://github.com/evan) ([#17](https://github.com/rwarner/ha-quietcool-ble/pull/17))
- Feat: **Low Temp Threshold** max raised 90 → 115°F to expose the device's full supported range
- Threshold and humidity-speed writes now re-assert TH mode in a single atomic BLE operation, so a change takes effect immediately without a second connection round-trip

### v0.2.18
- Feat: **set the timer duration from Home Assistant** ([#15](https://github.com/rwarner/ha-quietcool-ble/issues/15)). New **Timer Hours** and **Timer Minutes** number entities write the fan's stored timer duration (via `SetTime`) without starting the fan. Turning the fan on now counts down from this duration instead of always forcing the firmware's 8-hour default — previously every HA turn-on reset the timer to 8h regardless of what the app had set. No more setting the duration in the QuietCool app and switching to Timer mode in HA

### v0.2.17
- Feat: **discover name-less controllers.** Some controller revisions omit the BLE local name and advertise the manufacturer-specific signature `3atticfan` instead (BlueZ shows only the MAC). The integration now also matches this signature (`manufacturer_id` `0x6133`, payload prefix `tticfan`) alongside the normal `ATTICFAN*` name, so these fans auto-discover and appear in the manual picker; when only the MAC is known they show as `QuietCool Fan (<address>)` until the real name is read over GATT. Thanks [@viss](https://github.com/viss/ha-quietcool-ble) for reverse-engineering the manufacturer-data variant
- Minor: new setups now title the device from the name read over GATT (consistent with the fan-name entity), instead of the raw BLE advertisement name; existing entries are unchanged

### v0.2.16
- Fix: a **D-Bus connection leak** that could permanently kill Bluetooth after a few hours. Bleak does not close its per-connection D-Bus bus when the fan drops the BLE link on its own (its `_cleanup_all()` skips the bus close), leaking one dbus-daemon socket per reconnect cycle. Because the controller drops idle connections every ~25s, a busy setup hits the dbus default `max_connections_per_user=256` in ~1.7 hours — after which every Bluetooth connection is rejected (`[Errno 9] Bad file descriptor`, `EOFError`) and only a full Home Assistant restart recovers. The integration now closes the stale client's bus on a device-initiated disconnect, and force-closes it as a fallback if `disconnect()` times out. Thanks [@brian316](https://github.com/brian316) for the detailed root-cause trace and fix ([#13](https://github.com/rwarner/ha-quietcool-ble/pull/13))
- This may also be the underlying cause of [#10](https://github.com/rwarner/ha-quietcool-ble/issues/10), which reported the same symptom fingerprint (`Bad file descriptor` / `EOFError`, advertisements still arriving, recovers only on a full restart)

### v0.2.15
- Fix: the integration could **freeze permanently** — entities stopped updating and only a full Home Assistant restart recovered (a reload didn't). When the Bluetooth transport wedged (typically BlueZ/D-Bus, logging `EOFError` / `Bad file descriptor`), unbounded BLE calls never returned: a `disconnect()` held the connection lock forever, which also hung unload/reload. Every BLE call is now bounded by a timeout, disconnects never hold the lock, and a failed connection is dropped so the next poll reconnects instead of reusing a dead one. The integration now recovers on its own ([#10](https://github.com/rwarner/ha-quietcool-ble/issues/10))
- Fix: `homeassistant.update_entity` raised `AttributeError: ... has no attribute 'async_request_refresh'`. Manual refresh now works and triggers a real poll ([#10](https://github.com/rwarner/ha-quietcool-ble/issues/10))
- Fix: unloading the integration left a stray poll timer armed, which could fire against a dead coordinator and race the new one during a reload

Thanks [@romanmodin](https://github.com/romanmodin) for the detailed report.

### v0.2.14
- Fix: the integration now detects the fan's protocol (V1/V2) from the **login response** and corrects it at runtime. This fixes existing setups whose stored protocol was stale or mis-detected (e.g. after a firmware update) — the fan would connect fine but every sensor read "unavailable" because firmware 3.9+ silently ignores V1 poll commands. Thanks [@DillonBrown](https://github.com/DillonBrown) ([#9](https://github.com/rwarner/ha-quietcool-ble/pull/9))
- Fix: temperature/humidity samples that arrive encoded as strings are now parsed correctly (defensive numeric coercion)

### v0.2.13
- Fix: re-pairing now **reuses the existing Phone ID** instead of generating a new one each time. Controllers store at most **50** Phone IDs, so repeated re-pairs no longer risk filling that memory
- Feat: if the fan's pairing memory is full (`R:"Beyond"`), setup now shows a clear "factory-reset the controller" message instead of a generic failure
- Docs: new Troubleshooting note — pairing that's *acknowledged but never persists* usually means the 50-Phone-ID memory is full; factory-reset the controller to clear it. (This was the root cause of a firmware-4.1 pairing report — thanks to the community and [@CrazyCoder](https://github.com/CrazyCoder)'s protocol docs.)

### v0.2.12
- Fix: reloading or removing the integration crashed with `AttributeError: 'super' object has no attribute 'async_stop'` — the base coordinator has no `async_stop()` (its teardown is registered via `async_on_unload`). Removed the bad `super()` call. This also unbreaks the reauth flow's reload step ([#8](https://github.com/rwarner/ha-quietcool-ble/issues/8))
- Feat: the fan now also exposes percentage-based speed (`SET_SPEED`) mapped onto its Low/[Medium/]High steps, so the **HomeKit bridge** shows a working speed slider and the current running speed. The named presets remain available for HA control and automations ([#6](https://github.com/rwarner/ha-quietcool-ble/issues/6))
- Feat: the setup screen now accepts an optional **Phone ID** — enter a known ID (from a previous setup, an ESPHome config, or the QuietCool app) to skip pairing and just log in. This is the reliable path on firmware 3.9+ where pairing a *new* ID can fail, and it reflects that controllers store **multiple** Phone IDs, not a single slot ([#5](https://github.com/rwarner/ha-quietcool-ble/issues/5))
- Docs: corrected the pairing/connection docs — controllers store **multiple** Phone IDs (not a single slot), and the app and Home Assistant share one BLE connection at a time. Removed a duplicate Troubleshooting section and the inaccurate "⋮ → Re-authenticate" menu-button reference (re-pair is prompted automatically when the fan stops accepting our Phone ID)
- Thanks to [@CrazyCoder](https://github.com/CrazyCoder) for publishing authoritative [OEM BLE protocol documentation](https://github.com/CrazyCoder/quietcool-esphome-native/blob/main/docs/OEM-BLE-PROTOCOL.md), which confirmed the pairing/login sequence and Phone ID handling above. Their [ESPHome native firmware](https://github.com/CrazyCoder/quietcool-esphome-native) is a great alternative if BLE pairing is troublesome — see Related Projects

### v0.2.11
- Fix: the V2 pair command now sends the PhoneID under the short key `P` (`{"A":14,"P":…}`) instead of `PhoneID`, matching the QuietCool V2 protocol as implemented by `snyamathi/quietcool`. Debug logs from a firmware 4.1 fan showed the old form being rejected (`{"A":14,"R":"Fail"}`), which blocked pairing on newer firmware
- Also tolerates the V2 controller resetting the BLE connection in response to Pair (documented behavior on some firmware) — pairing is still confirmed by a login on a fresh connection
- Feat: when the controller stops accepting Home Assistant's Phone ID (e.g. after using the QuietCool app, or if the fan drops it), Home Assistant now raises a re-authentication prompt — a one-click re-pair — instead of leaving entities silently unavailable. Wires up the previously dormant reauth flow (`ConfigEntryAuthFailed`); reauth updates the existing entry's PhoneID instead of creating a duplicate
- Feat: **Download diagnostics** support on the device page (PhoneID, serial, and address redacted) — dumps firmware, protocol, `fan_type`, parameters, and current state to make issue reports easy
- Docs: pairing screen and Troubleshooting notes on re-pairing when the fan stops accepting Home Assistant's Phone ID

### v0.2.10
- Fix: pairing now tries the legacy (V1) pair **and** the V2 pair sequence, confirming **each** attempt with a login on a fresh connection. Previously, if the legacy pair was accepted for the pairing session but not truly persisted, the V2 sequence was never tried — so newly-paired firmware 3.9+ / V4.x fans could still end up permanently unavailable. Existing/working fans are unaffected (they succeed on the first attempt and never reach the V2 path)

### v0.2.9
- Fix: pairing is now confirmed with a login on a **fresh** BLE connection — the same way the coordinator connects on every poll. Previously the check reused the pairing connection, so a fan that accepted the PhoneID only for that session (but didn't persist it) could report success and then go unavailable. Follows up on the 0.2.8 pairing fix

### v0.2.8
- Fix: pairing on firmware 3.9+ / V4.x fans. The controller can acknowledge the legacy pair command without actually registering Home Assistant's PhoneID, leaving every entity permanently unavailable. Pairing now **verifies with a real login**, and if the legacy pair isn't accepted it sends the **V2 pair sequence** (PairMode → Pair). Reported on AFG SMT PRO-2.0 firmware 4.1
- Hardening: the config flow reports pairing success only when login actually works — a non-registering pair now fails clearly instead of creating a dead device
- More verbose pairing logs to aid diagnosis

### v0.2.7
- Medium speed is now **hardware-confirmed** on the AFG SMT ES-3.0 (firmware V4.1): the fan reports `FanType: THREE` and accepts `MEDIUM` as a speed — matching the values shipped in 0.2.6 ([#4](https://github.com/rwarner/ha-quietcool-ble/issues/4))
- Fix: the **Fan Speed** sensor now reports `Medium` on 3-speed fans — previously a 3-speed fan running at medium would have shown `Off`. Completes the medium-speed support added in 0.2.6
- Docs: supported-devices table, feature list, and entities table now reflect Medium speed on 3-speed fans

### v0.2.6
- Feat: Medium speed preset for 3-speed fans (e.g. AFG SMT ES-3.0). Only shown when the firmware reports a 3-speed `FanType`; 2-speed fans are unaffected and still show Low / High only ([#4](https://github.com/rwarner/ha-quietcool-ble/issues/4))
- Add `fan_type` diagnostic attribute to the fan entity, exposing the firmware-reported speed-count token so 3-speed support can be confirmed in the field
- Note: the BLE value for medium (`"MEDIUM"`) and the 3-speed token (`"THREE"`) are best-guesses pending hardware confirmation on a 3-speed unit

### v0.2.5
- Feat: full firmware 3.9+ / V2 protocol support — temperature, humidity, timer, and all threshold sensors now work on V4.x devices (thanks [@DillonBrown](https://github.com/DillonBrown))
- All V2 numeric API codes mapped from QuietCool Smart Control Android app 2.0.28: `GetWorkState`, `GetVersion`, `GetParameter`, `GetRemainTime`, `SetMode`, `SetTime`, `SetTempHumidity`
- Login now correctly parses compact V2 responses (`R`/`P` keys)

### v0.2.4
- Fix: unsolicited BLE notify messages from the device no longer flood the HA error log with `QueueFull` exceptions — excess messages are silently discarded
- Fix: if the ESPHome proxy TCP connection drops during idle disconnect, the coordinator now always cleans up the client reference and schedules a retry — previously this left polling dead until HA restarted

### v0.2.3
- Add "Fan Speed" sensor (`Off` / `Low` / `High`) showing physical running state, independent of control mode — useful in TH mode where the fan cycles automatically
- Fix: transient BLE GATT errors (e.g. ESPHome proxy error 133) no longer appear as ERROR in the HA log — already handled internally with backoff retry

### v0.2.2
- Fix: polling could halt permanently if the device held the BLE connection open long enough for the coordinator's 60s idle-disconnect timer to fire first. The idle disconnect was marked "expected" so no follow-up poll was ever scheduled, silencing all entity updates until HA restarted.

### v0.2.1
- Fix: poll halt on unexpected errors; stuck timer in TH mode; raised minimum HA version

### v0.2.0
- Full entity suite: fan control, smart mode (TH), temperature, humidity, timer, threshold configuration
- Hardware-confirmed BLE protocol on AFG SMT PRO-2.0

### v0.1.0
- Initial release
