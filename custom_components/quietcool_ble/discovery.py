"""QuietCool Bluetooth advertisement matching helpers.

Most controllers advertise a BLE local name beginning with ``ATTICFAN`` (mains
attic fans). Some revisions omit the name entirely and instead broadcast the
manufacturer-specific signature ``3atticfan`` (company ID 0x6133, payload prefix
``tticfan``); BlueZ then surfaces only the MAC address. These helpers recognise
both variants and produce a sensible display name when only the address is
available. Solar models (``SOLARFAN-*``) are intentionally excluded: they run
Bluetooth Mesh rather than this vendor protocol (#19).

Credit: the manufacturer-data variant was reverse-engineered by
[@viss](https://github.com/viss/ha-quietcool-ble).
"""
from __future__ import annotations

from typing import Any

from .const import (
    BLE_MANUFACTURER_ID,
    BLE_MANUFACTURER_PREFIX,
    BLE_NAME_PREFIXES,
)


def is_quietcool_advertisement(service_info: Any) -> bool:
    """Return whether an advertisement matches a known QuietCool controller.

    Matches an ``ATTICFAN`` local-name prefix or the ``tticfan``
    manufacturer-data payload under company ID 0x6133.
    """
    name = getattr(service_info, "name", None)
    if name and name.startswith(BLE_NAME_PREFIXES):
        return True

    manufacturer_data = getattr(service_info, "manufacturer_data", None)
    if manufacturer_data is None:
        # Fall back to the raw advertisement surface if the discovery object
        # doesn't expose manufacturer_data directly.
        advertisement = getattr(service_info, "advertisement", None)
        # `or {}` guards the present-but-None case, not just a missing attribute.
        manufacturer_data = getattr(advertisement, "manufacturer_data", {}) or {}
    payload = manufacturer_data.get(BLE_MANUFACTURER_ID)
    return bool(payload and payload.startswith(BLE_MANUFACTURER_PREFIX))


def quietcool_display_name(service_info: Any) -> str:
    """Return a useful name when BlueZ exposes only the controller address.

    Named controllers keep their advertised name; name-less ones (where BlueZ
    reports the MAC as the name) get a readable ``QuietCool Fan (<address>)``.
    """
    name = getattr(service_info, "name", None)
    address = getattr(service_info, "address", "Unknown")
    if name and name != address:
        return name
    return f"QuietCool Fan ({address})"
