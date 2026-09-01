"""Constants for the QuietCool BLE integration."""
from __future__ import annotations

from typing import Final

DOMAIN: Final = "quietcool_ble"
PLATFORMS: Final = ["fan", "sensor", "select", "number"]

# FanType tokens (firmware GetParameter "FanType") known to expose a third,
# medium speed. Deliberately an allowlist: any fan that does NOT explicitly
# report one of these — 2-speed fans ("TWO"), unknown/garbage values, and older
# firmware that omits FanType — falls through to Low/High only and can never be
# shown or sent MEDIUM. Shared by the fan preset and the humidity-speed select so
# the two platforms can never disagree on what speeds a fan supports.
THREE_SPEED_FAN_TYPES: Final = frozenset({"THREE"})

# BLE protocol identifiers
SERVICE_UUID: Final = "000000ff-0000-1000-8000-00805f9b34fb"
CHAR_UUID: Final = "0000ff01-0000-1000-8000-00805f9b34fb"
BLE_NAME_PREFIX: Final = "ATTICFAN"
# Some controller revisions omit the BLE local name and instead advertise the
# bytes ``3atticfan`` as manufacturer-specific data. BLE consumes the leading
# little-endian ``3a`` as company ID 0x6133, leaving ``tticfan`` as the payload.
# Discovery matches this signature so those name-less controllers still appear.
BLE_MANUFACTURER_ID: Final = 0x6133
BLE_MANUFACTURER_PREFIX: Final = b"tticfan"

# Polling and connection
POLL_INTERVAL_SECONDS: Final = 10
KEEP_ALIVE_SECONDS: Final = 60   # idle BLE connection timeout before voluntary disconnect
COMMAND_TIMEOUT: Final = 10.0    # seconds per BLE round-trip
MAX_RECV_BUFFER: Final = 1024    # bytes; all real responses are <200 bytes
MAX_CONNECT_ATTEMPTS: Final = 2  # per establish_connection call; fast-fail, coordinator backsoff
# Bound every BLE disconnect: BleakClient.disconnect() can hang forever when the
# BlueZ D-Bus connection wedges, which used to hold _connect_lock and freeze the
# integration until HA restarted (issue #10). Our dependency bleak_retry_connector
# guards its own D-Bus calls the same way (its DISCONNECT_TIMEOUT = 5); we keep a
# local constant rather than importing theirs so a version without that symbol
# can't ImportError the whole integration at load time.
DISCONNECT_TIMEOUT: Final = 5.0

# Config entry data keys
CONF_PHONE_ID: Final = "phone_id"
CONF_FAN_NAME: Final = "fan_name"
CONF_FAN_MODEL: Final = "fan_model"
CONF_FAN_SERIAL: Final = "fan_serial"
CONF_PROTOCOL: Final = "protocol"
