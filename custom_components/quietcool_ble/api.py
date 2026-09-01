"""QuietCool BLE protocol implementation — dual-protocol support.

All communication uses a single GATT characteristic (bidirectional):
  Service:  000000ff-0000-1000-8000-00805f9b34fb
  Char:     0000ff01-0000-1000-8000-00805f9b34fb

TWO PROTOCOL VERSIONS EXIST based on firmware version:

  V1 (firmware < 3.9, e.g. V2.x):
    Commands: {"Api": "GetFanInfo"}
    Responses: {"Name": "...", "Model": "...", "SerialNum": "..."}

  V2 (firmware >= 3.9):
    Commands: {"A": 17}  (numeric API codes)
    Responses: QQ{"A": 17, "N": "...", "M": "...", "S": "..."}
    The "QQ" prefix must be stripped before JSON parsing.
    Response keys are single characters mapped to field names.

Protocol auto-detected on first GetFanInfo call: if the response contains
"N" (short key) rather than "Name" (long key), V2 is used for all subsequent
commands on that connection.

Temperature: Temp_Sample / 10 = °F (V1 key) or T / 10 = °F (V2 key).
Source: emerose/quietcool, reddit.com/r/homeassistant/comments/1kyv0pn (u/secretoftheeast)
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from enum import IntEnum, StrEnum

from bleak import BleakClient

from .const import CHAR_UUID, COMMAND_TIMEOUT, DISCONNECT_TIMEOUT, MAX_RECV_BUFFER

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Protocol version
# ---------------------------------------------------------------------------

class ProtocolVersion(StrEnum):
    V1 = "v1"  # firmware < 3.9: string command names, full response keys
    V2 = "v2"  # firmware >= 3.9: numeric command codes, single-char response keys


# ---------------------------------------------------------------------------
# V2 numeric API codes from QuietCool Smart Control Android app 2.0.28.
# ---------------------------------------------------------------------------

class ApiCode(IntEnum):
    GET_WORK_STATE = 1
    GET_PARAMETER = 2
    GET_VERSION = 3
    GET_ROUTER = 4
    GET_UPGRADE_STATE = 5
    SET_TEMP_HUMIDITY = 6
    SET_TIME = 7
    GET_REMAIN_TIME = 8
    SET_MODE = 9
    UPGRADE = 10
    SET_ROUTER = 11
    LOGIN = 13
    PAIR = 14
    PAIR_MODE = 15
    SET_FAN_INFO = 16
    GET_FAN_INFO = 17
    SET_SPEED = 18
    GET_PRESETS = 19
    SET_PRESETS = 20
    SET_GUIDE_SETUP = 21
    RESET = 22


# V2 response key → semantic name mapping (confirmed from u/secretoftheeast)
_V2_FAN_INFO_KEYS = {
    "N": "Name",
    "M": "Model",
    "S": "SerialNum",
    "G": "GuideSetup",
    "A": "_api_code",
}

_V2_WORK_STATE_KEYS: dict[str, str] = {
    "T": "Temp_Sample",
    "S": "SensorState",
    "R": "Range",
    "M": "Mode",
    "H": "Humidity_Sample",
    "C": "ControlType",
}

_V2_PARAMETER_KEYS: dict[str, str] = {
    "M": "GuideSetup",
    "L": "GetTime_Range",
    "K": "GetMinute",
    "J": "GetHour",
    "I": "GetHum_Range",
    "H": "GetHum_L",
    "G": "GetHum_H",
    "F": "GetTemp_L",
    "E": "GetTemp_M",
    "D": "GetTemp_H",
    "C": "FanType",
    "B": "Mode",
}

_V2_VERSION_KEYS: dict[str, str] = {
    "V": "Version",
    "P": "ProtectTemp",
    "M": "Create_Mode",
    "H": "MCU_Version",
    "D": "Create_Date",
}

_V2_REMAIN_TIME_KEYS: dict[str, str] = {
    "S": "RemainSecond",
    "M": "RemainMinute",
    "H": "RemainHour",
}


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

class FanMode(StrEnum):
    IDLE = "Idle"
    TIMER = "Timer"
    TH = "TH"  # Thermostat+Humidity smart mode


class FanSpeed(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"  # 3-speed ECM fans only (e.g. AFG SMT ES-3.0); hardware-confirmed on firmware V4.1 (#4)
    LOW = "LOW"


@dataclass(frozen=True, slots=True)
class FanState:
    mode: str
    range: str | None
    temp_fahrenheit: float | None   # Temp_Sample / 10; None if sensor absent/error
    humidity_percent: float | None  # Humidity_Sample / 10; None if sensor absent/error
    sensor_state: str | None = None  # "Normal", "Error", absent on older firmware
    protocol: str = ProtocolVersion.V1
    remain_seconds: int = 0  # RemainHour*3600 + RemainMinute*60 + RemainSecond


@dataclass(frozen=True, slots=True)
class FanInfo:
    name: str
    model: str
    serial: str
    protocol: str = ProtocolVersion.V1  # detected during get_fan_info()


@dataclass(frozen=True, slots=True)
class FanVersion:
    firmware: str    # e.g. "IT-BLT-ATTICFAN_V3.0"
    hw_version: str  # e.g. "A"
    protect_temp: int  # °F overtemp safety cutoff, e.g. 182
    create_date: str   # e.g. "2023.07.25"


# Device sentinel: a threshold field set to 255 means "this tier is disabled".
# Applies to the humidity turn-on (hum_l) in particular.
HUM_DISABLED = 255


@dataclass(frozen=True, slots=True)
class FanParameters:
    temp_h: int        # high temp threshold °F (smart mode activates above this)
    temp_m: int        # medium temp threshold °F (2-speed fans: switches from LOW to HIGH)
    temp_l: int        # low temp threshold °F (smart mode deactivates below this)
    hum_h: int         # "Turn Fan Off" humidity cutout %: fan STOPS at/above this (factory 90)
    hum_l: int         # "Turn Fan On" humidity trigger %: fan RUNS above this (factory 70; 255 = disabled)
    hum_range: str     # fan speed for humidity-driven runs, "HIGH" or "LOW" ("MEDIUM" on 3-speed)
    fan_type: str      # "TWO" = 2-speed
    timer_hour: int    # default timer hours
    timer_minute: int  # default timer minutes
    timer_range: str   # fan speed for timer mode, "HIGH" or "LOW"


@dataclass(frozen=True, slots=True)
class LoginResult:
    authenticated: bool
    protocol: str


class QuietCoolError(Exception):
    """Base error for QuietCool BLE protocol errors."""


class AuthenticationError(QuietCoolError):
    """Raised when login is rejected (wrong or unregistered PhoneID)."""


class UnsupportedProtocolError(QuietCoolError):
    """Raised when the device uses V2 protocol for a command not yet mapped."""


# ---------------------------------------------------------------------------
# Public API — protocol-transparent
# ---------------------------------------------------------------------------

async def safe_disconnect(client: BleakClient, label: str = "") -> None:
    """Disconnect a BLE client with a bounded timeout. Never raises.

    BleakClient.disconnect() can hang forever when the BlueZ D-Bus connection
    wedges (issue #10) — and an unbounded disconnect while holding the
    coordinator's _connect_lock froze the integration until HA was restarted.
    Every disconnect in this integration must go through here.
    """
    try:
        await asyncio.wait_for(client.disconnect(), timeout=DISCONNECT_TIMEOUT)
    except asyncio.TimeoutError:
        _LOGGER.warning(
            "QuietCool %s: BLE disconnect did not complete within %.0fs; "
            "abandoning the connection",
            label or client.address,
            DISCONNECT_TIMEOUT,
        )
        _force_close_bus(client)
    except Exception as err:  # noqa: BLE001 — teardown must never raise
        _LOGGER.debug(
            "QuietCool %s: error during BLE disconnect (ignored): %s",
            label or client.address,
            err,
        )
        _force_close_bus(client)


def _force_close_bus(client: BleakClient) -> None:
    """Force-close a BleakClient's D-Bus bus to prevent connection leak.

    Bleak's _cleanup_all() does not close the per-client D-Bus MessageBus when
    the BLE device disconnects on its own, leaking one Unix socket to dbus-daemon
    per reconnect cycle.  This reaches into Bleak internals as a last resort when
    the normal disconnect() path fails or was never called.
    """
    try:
        backend = getattr(client, "_backend", None)
        bus = getattr(backend, "_bus", None) if backend else None
        if bus is not None:
            bus.disconnect()
            _LOGGER.debug("QuietCool: force-closed leaked D-Bus bus")
    except Exception:  # noqa: BLE001 — best-effort cleanup
        pass


async def login(client: BleakClient, phone_id: str) -> bool:
    """Send Login. Returns True if authenticated, False if pairing needed.

    Firmware 3.9+ accepts the V1 command body but responds with compact V2
    keys: {"A": 13, "R": "Success", "P": "No"}.
    """
    return (await login_with_protocol(client, phone_id)).authenticated


async def login_with_protocol(client: BleakClient, phone_id: str) -> LoginResult:
    """Send Login and return both authentication result and detected protocol."""
    resp = await _send_command(client, {"Api": "Login", "PhoneID": phone_id})
    result = resp.get("Result", resp.get("R"))
    pair_state = resp.get("PairState", resp.get("P"))
    protocol = (
        ProtocolVersion.V2
        if resp.get("A") == ApiCode.LOGIN or "R" in resp or "P" in resp
        else ProtocolVersion.V1
    )
    if result == "Success":
        return LoginResult(True, protocol)
    if pair_state is not None:
        return LoginResult(False, protocol)
    _LOGGER.warning("Unexpected login response: %s", resp)
    return LoginResult(False, protocol)


async def pair_v1(client: BleakClient, phone_id: str) -> str | None:
    """Send the legacy V1 Pair command. Registers the PhoneID on firmware < 3.9.

    This only sends the command — the caller must confirm the PhoneID actually
    persisted by logging in on a *fresh* connection. The controller ACKs a Pair
    command even when it does not store the PhoneID, so the ACK is not trusted.

    Returns the Pair result string (`"Success"`, `"Fail"`, `"Beyond"` = the fan's
    50-PhoneID memory is full), or None if there was no parseable response.
    """
    resp = await _send_command(client, {"Api": "Pair", "PhoneID": phone_id})
    _LOGGER.debug("QuietCool pair: V1 Pair response: %s", resp)
    return resp.get("Result", resp.get("R"))


async def pair_v2(client: BleakClient, phone_id: str) -> str | None:
    """Send the V2 pair sequence (PairMode then Pair) for firmware 3.9+ / V4.x.

    The V2 Pair command is numeric code 14 with the PhoneID under the short key
    ``"P"`` (not ``"PhoneID"``) — matching snyamathi/quietcool, whose V2 support
    is confirmed on V4.1 hardware. Some V2 firmware also resets the BLE
    connection instead of replying to Pair, so a missing/failed response is not
    treated as fatal — the caller confirms with a fresh-connection login.

    Returns the Pair result string (see pair_v1), or None on no response.
    """
    try:
        pm = await _send_command(client, {"A": ApiCode.PAIR_MODE})
        _LOGGER.debug("QuietCool pair: V2 PairMode response: %s", pm)
    except TimeoutError:
        # PairMode does not reply on some firmware — that is fine, keep going.
        _LOGGER.debug("QuietCool pair: V2 PairMode sent (no response)")
    try:
        resp = await _send_command(client, {"A": ApiCode.PAIR, "P": phone_id})
        _LOGGER.debug("QuietCool pair: V2 Pair response: %s", resp)
        return resp.get("Result", resp.get("R"))
    except TimeoutError:
        # V2 firmware may reset the connection instead of replying — expected.
        _LOGGER.debug("QuietCool pair: V2 Pair sent (no response — likely reset)")
        return None


async def get_fan_info(client: BleakClient) -> FanInfo:
    """Fetch device identification. Auto-detects V1 vs V2 protocol.

    V2 (firmware 3.9+): sends {"A": 17}, parses single-char response keys.
    V1 (firmware < 3.9): sends {"Api": "GetFanInfo"}, parses full-name keys.

    Returns FanInfo with .protocol indicating which version was detected.
    """
    # Try V2 first (firmware 3.9+) — if response has single-char keys, it's V2
    try:
        resp_v2 = await _send_command(client, {"A": ApiCode.GET_FAN_INFO})
        if "N" in resp_v2:
            _LOGGER.debug("Detected protocol V2 (firmware 3.9+) from GetFanInfo response")
            return FanInfo(
                name=str(resp_v2.get("N", "QuietCool Fan"))[:64].strip(),
                model=str(resp_v2.get("M", ""))[:64].strip(),
                serial=str(resp_v2.get("S", ""))[:64].strip(),
                protocol=ProtocolVersion.V2,
            )
        # V2 command returned V1-style keys (shouldn't happen but handle it)
        if "Name" in resp_v2:
            return FanInfo(
                name=str(resp_v2.get("Name", "QuietCool Fan"))[:64].strip(),
                model=str(resp_v2.get("Model", ""))[:64].strip(),
                serial=str(resp_v2.get("SerialNum", ""))[:64].strip(),
                protocol=ProtocolVersion.V1,
            )
    except (TimeoutError, QuietCoolError):
        _LOGGER.debug("V2 GetFanInfo timed out; falling back to V1")

    # Fall back to V1 protocol
    resp_v1 = await _send_command(client, {"Api": "GetFanInfo"})
    return FanInfo(
        name=str(resp_v1.get("Name", "QuietCool Fan"))[:64].strip(),
        model=str(resp_v1.get("Model", ""))[:64].strip(),
        serial=str(resp_v1.get("SerialNum", ""))[:64].strip(),
        protocol=ProtocolVersion.V1,
    )


async def get_work_state(client: BleakClient, protocol: str = ProtocolVersion.V1) -> FanState:
    """Poll current fan operating state including temperature and humidity.

    Pass the protocol version detected during get_fan_info() to use the
    correct wire format.

    V2 command/key mapping was recovered from QuietCool Smart Control
    Android app 2.0.28.
    """
    if protocol == ProtocolVersion.V2:
        resp = await _send_command(client, {"A": ApiCode.GET_WORK_STATE})
        resp = _normalize_v2_response(resp, _V2_WORK_STATE_KEYS, "GetWorkState")
    else:
        resp = await _send_command(client, {"Api": "GetWorkState"})

    # Log any keys we don't recognise — helps discover undocumented fields.
    _KNOWN_WORK_STATE_KEYS = {
        "Api", "Mode", "Range", "SensorState", "Temp_Sample", "Humidity_Sample",
    }
    unknown = {k: v for k, v in resp.items() if k not in _KNOWN_WORK_STATE_KEYS}
    if unknown:
        _LOGGER.info("QuietCool GetWorkState unknown fields: %s", unknown)

    raw_temp = _coerce_number(resp.get("Temp_Sample"))
    raw_hum = _coerce_number(resp.get("Humidity_Sample"))
    return FanState(
        mode=resp.get("Mode", FanMode.IDLE),
        range=resp.get("Range"),
        temp_fahrenheit=(
            raw_temp / 10
            if raw_temp is not None and 0 <= raw_temp <= 2000
            else None
        ),
        humidity_percent=(
            float(raw_hum)
            if raw_hum is not None and 0 <= raw_hum <= 100
            else None
        ),
        sensor_state=resp.get("SensorState"),
        protocol=protocol,
    )


async def get_version(client: BleakClient) -> dict:
    """Fetch firmware version string. Returns raw response dict."""
    return await _send_command(client, {"Api": "GetVersion"})


async def get_parameter(client: BleakClient) -> dict:
    """Fetch device parameters — likely temperature thresholds for smart mode."""
    return await _send_command(client, {"Api": "GetParameter"})


async def get_remain_time(client: BleakClient, protocol: str = ProtocolVersion.V1) -> dict:
    """Fetch remaining timer countdown."""
    if protocol == ProtocolVersion.V2:
        resp = await _send_command(client, {"A": ApiCode.GET_REMAIN_TIME})
        return _normalize_v2_response(resp, _V2_REMAIN_TIME_KEYS, "GetRemainTime")
    return await _send_command(client, {"Api": "GetRemainTime"})


async def get_version_info(
    client: BleakClient,
    protocol: str = ProtocolVersion.V1,
) -> FanVersion:
    """Fetch firmware version and device metadata."""
    if protocol == ProtocolVersion.V2:
        resp = await _send_command(client, {"A": ApiCode.GET_VERSION})
        resp = _normalize_v2_response(resp, _V2_VERSION_KEYS, "GetVersion")
    else:
        resp = await _send_command(client, {"Api": "GetVersion"})
    return FanVersion(
        firmware=str(resp.get("Version", ""))[:64].strip(),
        hw_version=str(resp.get("HW_Version", resp.get("MCU_Version", "")))[:16].strip(),
        protect_temp=int(resp.get("ProtectTemp", 0)),
        create_date=str(resp.get("Create_Date", ""))[:32].strip(),
    )


async def get_parameters(
    client: BleakClient,
    protocol: str = ProtocolVersion.V1,
) -> FanParameters:
    """Fetch smart mode and timer threshold parameters."""
    if protocol == ProtocolVersion.V2:
        resp = await _send_command(client, {"A": ApiCode.GET_PARAMETER})
        resp = _normalize_v2_response(resp, _V2_PARAMETER_KEYS, "GetParameter")
    else:
        resp = await _send_command(client, {"Api": "GetParameter"})
    return FanParameters(
        temp_h=int(resp.get("GetTemp_H", 85)),
        temp_m=int(resp.get("GetTemp_M", 75)),
        temp_l=int(resp.get("GetTemp_L", 65)),
        hum_h=int(resp.get("GetHum_H", 90)),
        hum_l=int(resp.get("GetHum_L", HUM_DISABLED)),
        hum_range=str(resp.get("GetHum_Range", FanSpeed.LOW)),
        fan_type=str(resp.get("FanType", "TWO")),
        timer_hour=int(resp.get("GetHour", 8)),
        timer_minute=int(resp.get("GetMinute", 0)),
        timer_range=str(resp.get("GetTime_Range", FanSpeed.LOW)),
    )


async def set_mode_idle(client: BleakClient, protocol: str = ProtocolVersion.V1) -> None:
    """Turn the fan off (Idle mode)."""
    if protocol == ProtocolVersion.V2:
        await _send_command(client, {"A": ApiCode.SET_MODE, "M": FanMode.IDLE})
        return
    await _send_command(client, {"Api": "SetMode", "Mode": FanMode.IDLE})


async def set_timer(
    client: BleakClient,
    speed: str,
    hours: int = 8,
    minutes: int = 0,
    protocol: str = ProtocolVersion.V1,
) -> None:
    """Save the timer duration and speed WITHOUT changing the operating mode.

    Sends only SetTime, so the fan keeps its current mode and does not start —
    this configures the duration a later Timer activation will count down from.
    set_mode_timer() sends the same SetTime and then SetMode:Timer to actually
    start the countdown.
    """
    if speed not in (FanSpeed.HIGH, FanSpeed.MEDIUM, FanSpeed.LOW):
        raise ValueError(f"Invalid speed {speed!r}; must be 'HIGH', 'MEDIUM', or 'LOW'")
    if not (0 <= hours <= 23 and 0 <= minutes <= 59):
        raise ValueError(f"Invalid timer duration: {hours}h {minutes}m")
    if protocol == ProtocolVersion.V2:
        await _send_command(
            client,
            {"A": ApiCode.SET_TIME, "H": hours, "M": minutes, "R": speed},
        )
        return
    await _send_command(
        client,
        {
            "Api": "SetTime",
            "SetHour": hours,
            "SetMinute": minutes,
            "SetTime_Range": speed,
        },
    )


async def set_mode_timer(
    client: BleakClient,
    speed: str,
    hours: int = 8,
    minutes: int = 0,
    protocol: str = ProtocolVersion.V1,
) -> None:
    """Turn the fan on at the given speed for the given duration.

    Saves the duration/speed (SetTime) and then switches to Timer mode, which
    starts the countdown.
    """
    await set_timer(client, speed, hours, minutes, protocol)
    if protocol == ProtocolVersion.V2:
        await _send_command(client, {"A": ApiCode.SET_MODE, "M": FanMode.TIMER})
        return
    await _send_command(client, {"Api": "SetMode", "Mode": FanMode.TIMER})


# Firmware default timer duration, used when no duration has been configured.
DEFAULT_TIMER_HOURS = 8
DEFAULT_TIMER_MINUTES = 0


def resolve_timer_duration(params: FanParameters | None) -> tuple[int, int]:
    """Return the (hours, minutes) a Timer activation should count down from.

    Falls back to the firmware's 8-hour default when parameters haven't been
    fetched yet, or when the stored duration is 0h0m — so turning the fan on
    always runs for a sensible time instead of a no-op zero-length timer.
    """
    if params is None:
        return DEFAULT_TIMER_HOURS, DEFAULT_TIMER_MINUTES
    if params.timer_hour == 0 and params.timer_minute == 0:
        return DEFAULT_TIMER_HOURS, DEFAULT_TIMER_MINUTES
    return params.timer_hour, params.timer_minute


async def set_mode_th(client: BleakClient, protocol: str = ProtocolVersion.V1) -> None:
    """Activate smart Thermostat+Humidity (TH) mode."""
    if protocol == ProtocolVersion.V2:
        await _send_command(client, {"A": ApiCode.SET_MODE, "M": FanMode.TH})
        return
    await _send_command(client, {"Api": "SetMode", "Mode": FanMode.TH})


async def set_temp_humidity(
    client: BleakClient,
    *,
    temp_h: int,
    temp_m: int,
    temp_l: int,
    hum_h: int,
    hum_l: int,
    hum_range: str,
    protocol: str = ProtocolVersion.V1,
) -> None:
    """Write smart mode temperature/humidity thresholds to the device.

    Uses SetTempHumidity command (confirmed from emerose/quietcool source).
    All six fields are required — the device ignores partial writes.
    """
    if protocol == ProtocolVersion.V2:
        resp = await _send_command(
            client,
            {
                "A": ApiCode.SET_TEMP_HUMIDITY,
                "B": temp_h,
                "C": temp_m,
                "D": temp_l,
                "E": hum_h,
                "F": hum_l,
                "G": hum_range,
            },
        )
        _LOGGER.debug("SetTempHumidity response: %s", resp)
        return
    resp = await _send_command(
        client,
        {
            "Api": "SetTempHumidity",
            "SetTemp_H": temp_h,
            "SetTemp_M": temp_m,
            "SetTemp_L": temp_l,
            "SetHum_H": hum_h,
            "SetHum_L": hum_l,
            "SetHum_Range": hum_range,
        },
    )
    _LOGGER.debug("SetTempHumidity response: %s", resp)


def _normalize_v2_response(resp: dict, key_map: dict[str, str], api_name: str) -> dict:
    """Translate a compact V2 response into the V1-style field names."""
    normalized = {field: resp[key] for key, field in key_map.items() if key in resp}
    normalized["Api"] = api_name
    return normalized


def _coerce_number(value: object) -> float | None:
    """Return a numeric response value, accepting numbers encoded as strings."""
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


# ---------------------------------------------------------------------------
# Transport layer
# ---------------------------------------------------------------------------

def _strip_qq_prefix(data: bytes) -> tuple[bytes, bool]:
    """Strip the 'QQ' prefix added by firmware 3.9+ before JSON content.

    Returns (stripped_data, had_prefix). The had_prefix flag lets callers
    log when the prefix was detected without re-examining the bytes.
    """
    if data.startswith(b"QQ"):
        return data[2:], True
    return data, False


async def _send_command(client: BleakClient, payload: dict) -> dict:
    """Send a JSON command and await the device's notify response.

    start_notify is registered BEFORE the write to guarantee we cannot miss
    the response in the window between write-returning and notify-registration.

    Handles the firmware 3.9+ "QQ" prefix by stripping it before JSON parsing.
    """
    cmd_label = payload.get("Api") or f"A={payload.get('A')}"
    response_queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=4)
    recv_buffer = bytearray()
    _qq_seen = False

    def handle_notify(_: object, data: bytearray) -> None:
        nonlocal recv_buffer, _qq_seen
        _LOGGER.debug(
            "QuietCool BLE notify chunk (%d bytes): %s",
            len(data),
            data.hex(),
        )
        recv_buffer += data
        if len(recv_buffer) > MAX_RECV_BUFFER:
            _LOGGER.warning(
                "QuietCool BLE notify buffer overflow (%d bytes); resetting. "
                "Raw buffer: %s",
                len(recv_buffer),
                bytes(recv_buffer).hex(),
            )
            recv_buffer = bytearray()
            return
        candidate, had_qq = _strip_qq_prefix(bytes(recv_buffer))
        if had_qq and not _qq_seen:
            _qq_seen = True
            _LOGGER.debug(
                "QuietCool BLE: firmware 3.9+ QQ prefix detected on %s response",
                cmd_label,
            )
        try:
            msg = json.loads(candidate.decode("utf-8"))
            _LOGGER.debug(
                "QuietCool BLE %s ← %s (buffer %d bytes, qq=%s)",
                cmd_label,
                msg,
                len(recv_buffer),
                _qq_seen,
            )
            recv_buffer = bytearray()
            try:
                response_queue.put_nowait(msg)
            except asyncio.QueueFull:
                _LOGGER.debug(
                    "QuietCool BLE %s: notify queue full, discarding extra message",
                    cmd_label,
                )
        except (json.JSONDecodeError, UnicodeDecodeError):
            _LOGGER.debug(
                "QuietCool BLE %s: incomplete chunk, buffering (%d bytes so far)",
                cmd_label,
                len(recv_buffer),
            )

    # Register notify BEFORE writing.
    # Every GATT call here is wrapped in wait_for: on a wedged BLE transport these
    # can hang forever, and they run under the coordinator's _operation_lock — an
    # unbounded hang there froze all polling until HA restarted (issue #10).
    _LOGGER.debug("QuietCool BLE %s: registering notify", cmd_label)
    await asyncio.wait_for(
        client.start_notify(CHAR_UUID, handle_notify), timeout=COMMAND_TIMEOUT
    )
    try:
        raw = json.dumps(payload).encode("utf-8")
        _LOGGER.debug("QuietCool BLE %s → %s", cmd_label, raw.decode())
        char = client.services.get_characteristic(CHAR_UUID)
        chunk_size = char.max_write_without_response_size
        for i in range(0, len(raw), chunk_size):
            await asyncio.wait_for(
                client.write_gatt_char(
                    CHAR_UUID, raw[i : i + chunk_size], response=True
                ),
                timeout=COMMAND_TIMEOUT,
            )
        resp = await asyncio.wait_for(response_queue.get(), timeout=COMMAND_TIMEOUT)
        return resp
    except asyncio.TimeoutError as err:
        _LOGGER.warning(
            "QuietCool BLE %s: no response within %ss. "
            "Raw buffer at timeout: %s",
            cmd_label,
            COMMAND_TIMEOUT,
            bytes(recv_buffer).hex() if recv_buffer else "<empty>",
        )
        raise TimeoutError(
            f"No BLE response to '{cmd_label}' within {COMMAND_TIMEOUT}s"
        ) from err
    finally:
        # Bounded and swallowed: this runs on every command including the timeout
        # path, and a raising stop_notify would MASK the in-flight TimeoutError.
        try:
            await asyncio.wait_for(
                client.stop_notify(CHAR_UUID), timeout=COMMAND_TIMEOUT
            )
        except Exception as err:  # noqa: BLE001 — teardown must never mask/raise
            _LOGGER.debug(
                "QuietCool BLE %s: stop_notify failed (ignored): %s", cmd_label, err
            )
